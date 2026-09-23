package main

import (
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const maxBody = 128 * 1024
const maxMessage = 16 * 1024

type app struct {
	pool *pgxpool.Pool
	aead cipher.AEAD
}

func encryption(encoded string) (cipher.AEAD, error) {
	key, err := base64.StdEncoding.Strict().DecodeString(encoded)
	if err != nil || len(key) != 32 {
		return nil, errors.New("invalid encryption key configuration")
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}
func jsonResponse(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func failure(w http.ResponseWriter, status int, code string) {
	jsonResponse(w, status, map[string]string{"error": code})
}
func uuid() (string, error) {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return "", err
	}
	b[6] = (b[6] & 15) | 64
	b[8] = (b[8] & 63) | 128
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16]), nil
}
func validUUID(id string) bool {
	if len(id) != 36 || id[8] != '-' || id[13] != '-' || id[18] != '-' || id[23] != '-' {
		return false
	}
	_, err := hex.DecodeString(strings.ReplaceAll(id, "-", ""))
	return err == nil
}
func (a *app) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	defer func() {
		if recover() != nil {
			log.Print("request failed internally")
			failure(w, 500, "internal_error")
		}
	}()
	if r.Method == http.MethodPost && r.URL.Path == "/messages" {
		a.post(w, r)
		return
	}
	if r.Method == http.MethodGet && strings.HasPrefix(r.URL.Path, "/messages/") {
		a.get(w, r)
		return
	}
	failure(w, 404, "not_found")
}
func (a *app) post(w http.ResponseWriter, r *http.Request) {
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxBody))
	if err != nil {
		var limit *http.MaxBytesError
		if errors.As(err, &limit) {
			failure(w, 413, "payload_too_large")
		} else {
			failure(w, 400, "invalid_request")
		}
		return
	}
	var body map[string]json.RawMessage
	if !utf8.Valid(raw) || json.Unmarshal(raw, &body) != nil {
		failure(w, 400, "invalid_request")
		return
	}
	messageRaw, ok := body["message"]
	var message string
	if !ok || len(messageRaw) == 0 || messageRaw[0] != '"' || json.Unmarshal(messageRaw, &message) != nil {
		failure(w, 400, "invalid_request")
		return
	}
	if len(message) > maxMessage {
		failure(w, 413, "payload_too_large")
		return
	}
	id, err := uuid()
	if err != nil {
		failure(w, 500, "internal_error")
		return
	}
	nonce := make([]byte, 12)
	if _, err = rand.Read(nonce); err != nil {
		failure(w, 500, "internal_error")
		return
	}
	sealed := a.aead.Seal(nil, nonce, []byte(message), nil)
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	_, err = a.pool.Exec(ctx, "INSERT INTO messages (id, nonce, ciphertext, tag) VALUES ($1,$2,$3,$4)", id, nonce, sealed[:len(sealed)-16], sealed[len(sealed)-16:])
	if err != nil {
		log.Print("database insert failed")
		failure(w, 500, "internal_error")
		return
	}
	jsonResponse(w, 201, map[string]string{"id": id})
}
func (a *app) get(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimPrefix(r.URL.Path, "/messages/")
	if !validUUID(id) {
		failure(w, 400, "invalid_id")
		return
	}
	id = strings.ToLower(id)
	ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
	defer cancel()
	var nonce, ciphertext, tag []byte
	err := a.pool.QueryRow(ctx, "SELECT nonce, ciphertext, tag FROM messages WHERE id=$1", id).Scan(&nonce, &ciphertext, &tag)
	if errors.Is(err, pgx.ErrNoRows) {
		failure(w, 404, "not_found")
		return
	}
	if err != nil {
		log.Print("database select failed")
		failure(w, 500, "internal_error")
		return
	}
	if len(nonce) != 12 || len(tag) != 16 {
		failure(w, 500, "internal_error")
		return
	}
	plaintext, err := a.aead.Open(nil, nonce, append(ciphertext, tag...), nil)
	if err != nil || !utf8.Valid(plaintext) {
		failure(w, 500, "internal_error")
		return
	}
	jsonResponse(w, 200, map[string]string{"id": id, "message": string(plaintext)})
}
func run() error {
	aead, err := encryption(os.Getenv("ENCRYPTION_KEY_BASE64"))
	if err != nil {
		return err
	}
	databaseURL := os.Getenv("DATABASE_URL")
	if databaseURL == "" {
		return errors.New("DATABASE_URL is required")
	}
	cfg, err := pgxpool.ParseConfig(databaseURL)
	if err != nil {
		return errors.New("invalid database configuration")
	}
	max := 10
	if value := os.Getenv("DB_POOL_MAX"); value != "" {
		max, err = strconv.Atoi(value)
		if err != nil || max < 1 || max > 10 {
			return errors.New("DB_POOL_MAX must be between 1 and 10")
		}
	}
	cfg.MaxConns = int32(max)
	cfg.MinConns = 0
	cfg.ConnConfig.ConnectTimeout = 5 * time.Second
	cfg.ConnConfig.DefaultQueryExecMode = pgx.QueryExecModeExec
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return errors.New("database initialization failed")
	}
	defer pool.Close()
	if pool.Ping(ctx) != nil {
		return errors.New("database startup connection failed")
	}
	port := os.Getenv("PORT")
	if port == "" {
		port = "8080"
	}
	server := http.Server{Addr: "0.0.0.0:" + port, Handler: &app{pool: pool, aead: aead}, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 5 * time.Second, WriteTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second}
	log.Print("API listening")
	return server.ListenAndServe()
}
func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}
