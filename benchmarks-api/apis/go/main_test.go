package main

import (
	"encoding/base64"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestEncryption(t *testing.T) {
	for _, key := range []string{"", "broken", base64.StdEncoding.EncodeToString(make([]byte, 31))} {
		if _, err := encryption(key); err == nil {
			t.Fatal("accepted invalid key")
		}
	}
	aead, err := encryption(base64.StdEncoding.EncodeToString(make([]byte, 32)))
	if err != nil {
		t.Fatal(err)
	}
	for _, text := range []string{"", "á😀\n\"", strings.Repeat("a", 16384)} {
		nonce := make([]byte, 12)
		encrypted := aead.Seal(nil, nonce, []byte(text), nil)
		plain, err := aead.Open(nil, nonce, encrypted, nil)
		if err != nil || string(plain) != text {
			t.Fatal("roundtrip failed")
		}
		encrypted[len(encrypted)-1] ^= 1
		if _, err := aead.Open(nil, nonce, encrypted, nil); err == nil {
			t.Fatal("accepted altered tag")
		}
	}
}
func TestInvalidRequests(t *testing.T) {
	a := &app{}
	tests := []struct {
		body   string
		status int
	}{
		{`{}`, 400}, {`null`, 400}, {`[]`, 400}, {`{"message":null}`, 400}, {`{"message":1}`, 400}, {`{"message":true}`, 400},
		{`{"message":"ok"} {}`, 400}, {`{"message":`, 400},
		{`{"message":"` + strings.Repeat("á", 8193) + `"}`, 413},
		{strings.Repeat(" ", maxBody+1), 413},
	}
	for _, tc := range tests {
		r := httptest.NewRequest("POST", "/messages", strings.NewReader(tc.body))
		w := httptest.NewRecorder()
		a.ServeHTTP(w, r)
		if w.Code != tc.status {
			t.Errorf("got %d, wanted %d", w.Code, tc.status)
		}
		if w.Header().Get("Content-Type") != "application/json" {
			t.Fatal("missing content type")
		}
	}
}
func TestUUID(t *testing.T) {
	id, err := uuid()
	if err != nil || !validUUID(id) || id[14] != '4' {
		t.Fatal("invalid generated uuid")
	}
	for _, id := range []string{"", "abc", "00000000-0000-0000-0000-00000000000g"} {
		if validUUID(id) {
			t.Fatal("accepted invalid uuid")
		}
	}
}
