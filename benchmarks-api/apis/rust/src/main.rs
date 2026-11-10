use aes_gcm::{
    aead::{rand_core::RngCore, AeadInPlace, KeyInit, OsRng},
    Aes256Gcm, Nonce, Tag,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use bytes::Bytes;
use deadpool_postgres::{Manager, ManagerConfig, Pool, RecyclingMethod};
use http_body_util::{BodyExt, Full, Limited};
use hyper::{body::Incoming, service::service_fn, Method, Request, Response, StatusCode};
use hyper_util::rt::{TokioIo, TokioTimer};
use serde::Deserialize;
use serde_json::json;
use std::{convert::Infallible, env, sync::Arc, time::Duration};
use tokio::{net::TcpListener, time::timeout};
use tokio_postgres::NoTls;
use uuid::Uuid;

const BODY_MAX: usize = 128 * 1024;
const MESSAGE_MAX: usize = 16 * 1024;
const DB_TIMEOUT: Duration = Duration::from_secs(5);
struct State {
    cipher: Aes256Gcm,
    pool: Pool,
}
#[derive(Deserialize)]
struct Input {
    message: String,
}
type Reply = Response<Full<Bytes>>;

fn response(status: StatusCode, value: serde_json::Value) -> Reply {
    Response::builder()
        .status(status)
        .header("content-type", "application/json")
        .body(Full::new(Bytes::from(value.to_string())))
        .unwrap()
}
fn error(status: StatusCode, code: &str) -> Reply {
    response(status, json!({"error":code}))
}
fn internal() -> Reply {
    error(StatusCode::INTERNAL_SERVER_ERROR, "internal_error")
}
fn parse_message(body: &[u8]) -> Result<String, Reply> {
    if body.iter().find(|b| !b.is_ascii_whitespace()) != Some(&b'{') {
        return Err(error(StatusCode::BAD_REQUEST, "invalid_request"));
    }
    let input: Input = serde_json::from_slice(body)
        .map_err(|_| error(StatusCode::BAD_REQUEST, "invalid_request"))?;
    if input.message.len() > MESSAGE_MAX {
        return Err(error(StatusCode::PAYLOAD_TOO_LARGE, "payload_too_large"));
    }
    Ok(input.message)
}
fn cipher_from_key(key: &str) -> Result<Aes256Gcm, &'static str> {
    let key = STANDARD.decode(key).map_err(|_| "invalid encryption key")?;
    Aes256Gcm::new_from_slice(&key).map_err(|_| "invalid encryption key")
}
fn encrypt(cipher: &Aes256Gcm, message: &str) -> Result<(Vec<u8>, Vec<u8>, Vec<u8>), ()> {
    let mut nonce = vec![0; 12];
    OsRng.try_fill_bytes(&mut nonce).map_err(|_| ())?;
    let mut ciphertext = message.as_bytes().to_vec();
    let tag = cipher
        .encrypt_in_place_detached(Nonce::from_slice(&nonce), b"", &mut ciphertext)
        .map_err(|_| ())?;
    Ok((nonce, ciphertext, tag.to_vec()))
}
fn decrypt(cipher: &Aes256Gcm, nonce: &[u8], ciphertext: &[u8], tag: &[u8]) -> Result<String, ()> {
    if nonce.len() != 12 || tag.len() != 16 {
        return Err(());
    }
    let mut plaintext = ciphertext.to_vec();
    cipher
        .decrypt_in_place_detached(
            Nonce::from_slice(nonce),
            b"",
            &mut plaintext,
            Tag::from_slice(tag),
        )
        .map_err(|_| ())?;
    String::from_utf8(plaintext).map_err(|_| ())
}
async fn route(req: Request<Incoming>, state: Arc<State>) -> Result<Reply, Infallible> {
    Ok(handle(req, &state).await)
}
async fn handle(req: Request<Incoming>, state: &State) -> Reply {
    if req
        .headers()
        .get("content-length")
        .and_then(|h| h.to_str().ok())
        .and_then(|h| h.parse::<usize>().ok())
        .is_some_and(|n| n > BODY_MAX)
    {
        return error(StatusCode::PAYLOAD_TOO_LARGE, "payload_too_large");
    }
    let method = req.method().clone();
    let path = req.uri().path().to_owned();
    let body = match timeout(
        Duration::from_secs(5),
        Limited::new(req.into_body(), BODY_MAX).collect(),
    )
    .await
    {
        Ok(Ok(body)) => body.to_bytes(),
        Ok(Err(e)) if e.is::<http_body_util::LengthLimitError>() => {
            return error(StatusCode::PAYLOAD_TOO_LARGE, "payload_too_large")
        }
        _ => return error(StatusCode::BAD_REQUEST, "invalid_request"),
    };
    if method == Method::POST && path == "/messages" {
        let message = match parse_message(&body) {
            Ok(m) => m,
            Err(e) => return e,
        };
        let id = Uuid::new_v4();
        let (nonce, ciphertext, tag) = match encrypt(&state.cipher, &message) {
            Ok(v) => v,
            Err(_) => return internal(),
        };
        let result = timeout(DB_TIMEOUT, async {
            let client = state.pool.get().await.map_err(|_| ())?;
            client
                .execute(
                    "INSERT INTO messages (id, nonce, ciphertext, tag) VALUES ($1, $2, $3, $4)",
                    &[&id, &nonce, &ciphertext, &tag],
                )
                .await
                .map_err(|_| ())
        })
        .await;
        return match result {
            Ok(Ok(_)) => response(StatusCode::CREATED, json!({"id":id.to_string()})),
            _ => internal(),
        };
    }
    if method == Method::GET && path.starts_with("/messages/") {
        let raw = &path[10..];
        let id = match Uuid::parse_str(raw) {
            Ok(id) if raw.len() == 36 && id.hyphenated().to_string().eq_ignore_ascii_case(raw) => {
                id
            }
            _ => return error(StatusCode::BAD_REQUEST, "invalid_id"),
        };
        let result = timeout(DB_TIMEOUT, async {
            let client = state.pool.get().await.map_err(|_| ())?;
            client
                .query_opt(
                    "SELECT nonce, ciphertext, tag FROM messages WHERE id = $1",
                    &[&id],
                )
                .await
                .map_err(|_| ())
        })
        .await;
        let row = match result {
            Ok(Ok(Some(row))) => row,
            Ok(Ok(None)) => return error(StatusCode::NOT_FOUND, "not_found"),
            _ => return internal(),
        };
        let values = (
            row.try_get::<_, Vec<u8>>(0),
            row.try_get::<_, Vec<u8>>(1),
            row.try_get::<_, Vec<u8>>(2),
        );
        if let (Ok(nonce), Ok(ciphertext), Ok(tag)) = values {
            if let Ok(message) = decrypt(&state.cipher, &nonce, &ciphertext, &tag) {
                return response(
                    StatusCode::OK,
                    json!({"id":id.to_string(), "message":message}),
                );
            }
        }
        return internal();
    }
    error(StatusCode::NOT_FOUND, "not_found")
}
#[tokio::main(flavor = "multi_thread", worker_threads = 1)]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let cipher =
        cipher_from_key(&env::var("ENCRYPTION_KEY_BASE64").map_err(|_| "missing encryption key")?)?;
    let mut config: tokio_postgres::Config = env::var("DATABASE_URL")
        .map_err(|_| "missing database URL")?
        .parse()
        .map_err(|_| "invalid database URL")?;
    config.connect_timeout(DB_TIMEOUT);
    config.options("-c statement_timeout=5000 -c synchronous_commit=on");
    let max: usize = env::var("DB_POOL_MAX")
        .unwrap_or_else(|_| "10".into())
        .parse()?;
    if !(1..=10).contains(&max) {
        return Err("DB_POOL_MAX must be between 1 and 10".into());
    }
    let manager = Manager::from_config(
        config,
        NoTls,
        ManagerConfig {
            recycling_method: RecyclingMethod::Fast,
        },
    );
    let pool = Pool::builder(manager)
        .max_size(max)
        .runtime(deadpool_postgres::Runtime::Tokio1)
        .build()?;
    let _ = timeout(DB_TIMEOUT, pool.get())
        .await
        .map_err(|_| "database startup timeout")?
        .map_err(|_| "database unavailable")?;
    let state = Arc::new(State { cipher, pool });
    let port: u16 = env::var("PORT").unwrap_or_else(|_| "8080".into()).parse()?;
    let listener = TcpListener::bind(("0.0.0.0", port)).await?;
    eprintln!("API listening on port {port}");
    loop {
        let (stream, _) = listener.accept().await?;
        stream.set_nodelay(true)?;
        let state = state.clone();
        tokio::spawn(async move {
            let mut builder = hyper::server::conn::http1::Builder::new();
            builder
                .keep_alive(true)
                .timer(TokioTimer::new())
                .header_read_timeout(Duration::from_secs(5));
            let _ = builder
                .serve_connection(
                    TokioIo::new(stream),
                    service_fn(move |req| route(req, state.clone())),
                )
                .await;
        });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn crypto_roundtrip_and_tampering() {
        let cipher = cipher_from_key(&STANDARD.encode([42; 32])).unwrap();
        for message in ["", "hello", "ação 😀\n\"\\"] {
            let (nonce, ciphertext, tag) = encrypt(&cipher, message).unwrap();
            assert_eq!(
                decrypt(&cipher, &nonce, &ciphertext, &tag).unwrap(),
                message
            );
            let mut bad_tag = tag.clone();
            bad_tag[0] ^= 1;
            assert!(decrypt(&cipher, &nonce, &ciphertext, &bad_tag).is_err());
            if !ciphertext.is_empty() {
                let mut bad = ciphertext.clone();
                bad[0] ^= 1;
                assert!(decrypt(&cipher, &nonce, &bad, &tag).is_err());
            }
            assert!(decrypt(&cipher, &[], &ciphertext, &tag).is_err());
        }
        assert!(cipher_from_key("invalid").is_err());
        assert!(cipher_from_key(&STANDARD.encode([0; 31])).is_err());
    }
    #[test]
    fn validation() {
        for body in [r#"{}"#, r#"["hello"]"#, r#"{"message":null}"#, r#"{"message":1}"#, "{"] {
            assert_eq!(
                parse_message(body.as_bytes()).unwrap_err().status(),
                StatusCode::BAD_REQUEST
            );
        }
        assert_eq!(parse_message(br#"{"message":"","extra":1}"#).unwrap(), "");
        assert!(parse_message(json!({"message":"é".repeat(8192)}).to_string().as_bytes()).is_ok());
        assert_eq!(
            parse_message(json!({"message":"é".repeat(8193)}).to_string().as_bytes())
                .unwrap_err()
                .status(),
            StatusCode::PAYLOAD_TOO_LARGE
        );
    }
}
