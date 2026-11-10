import com.google.gson.*;
import com.zaxxer.hikari.*;
import com.sun.net.httpserver.*;
import javax.crypto.Cipher;
import javax.crypto.spec.*;
import java.net.*;
import java.nio.*;
import java.nio.charset.*;
import java.security.SecureRandom;
import java.sql.*;
import java.util.*;
import java.util.concurrent.Executors;
import java.io.*;

public final class Main {
    static final int BODY_LIMIT = 128 * 1024, MESSAGE_LIMIT = 16 * 1024;
    static final Gson JSON = new GsonBuilder().setStrictness(Strictness.STRICT).create();
    static final SecureRandom RANDOM = new SecureRandom();
    static HikariDataSource pool;
    static SecretKeySpec key;

    static byte[] keyBytes(String encoded) {
        if (encoded == null) throw new IllegalArgumentException();
        byte[] value = Base64.getDecoder().decode(encoded);
        if (value.length != 32) throw new IllegalArgumentException();
        return value;
    }
    static byte[] utf8(String value) throws CharacterCodingException {
        ByteBuffer encoded = StandardCharsets.UTF_8.newEncoder()
            .onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT)
            .encode(CharBuffer.wrap(value));
        byte[] result = new byte[encoded.remaining()]; encoded.get(result); return result;
    }
    static String text(byte[] value) throws CharacterCodingException {
        return StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(value)).toString();
    }
    static byte[] crypt(int mode, byte[] nonce, byte[] value) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(mode, key, new GCMParameterSpec(128, nonce));
        return cipher.doFinal(value);
    }
    static void respond(HttpExchange exchange, int status, Object result) throws IOException {
        byte[] body = JSON.toJson(result).getBytes(StandardCharsets.UTF_8);
        exchange.getResponseHeaders().set("Content-Type", "application/json");
        exchange.sendResponseHeaders(status, body.length);
        exchange.getResponseBody().write(body);
    }
    static void error(HttpExchange exchange, int status, String code) throws IOException {
        respond(exchange, status, Map.of("error", code));
    }
    static void post(HttpExchange exchange) throws Exception {
        String length = exchange.getRequestHeaders().getFirst("Content-Length");
        if (length != null && Long.parseLong(length) > BODY_LIMIT) {
            exchange.getResponseHeaders().set("Connection", "close");
            error(exchange, 413, "payload_too_large"); return;
        }
        byte[] body = exchange.getRequestBody().readNBytes(BODY_LIMIT + 1);
        if (body.length > BODY_LIMIT) {
            exchange.getResponseHeaders().set("Connection", "close");
            error(exchange, 413, "payload_too_large"); return;
        }
        byte[] plaintext;
        try {
            JsonElement data = JSON.fromJson(text(body), JsonElement.class);
            if (data == null || !data.isJsonObject()) throw new IllegalArgumentException();
            JsonElement message = data.getAsJsonObject().get("message");
            if (message == null || !message.isJsonPrimitive() || !message.getAsJsonPrimitive().isString())
                throw new IllegalArgumentException();
            plaintext = utf8(message.getAsString());
        } catch (JsonParseException | IllegalArgumentException | CharacterCodingException exception) {
            error(exchange, 400, "invalid_request"); return;
        }
        if (plaintext.length > MESSAGE_LIMIT) { error(exchange, 413, "payload_too_large"); return; }
        UUID id = UUID.randomUUID();
        byte[] nonce = new byte[12]; RANDOM.nextBytes(nonce);
        byte[] encrypted = crypt(Cipher.ENCRYPT_MODE, nonce, plaintext);
        try (Connection connection = pool.getConnection(); PreparedStatement statement = connection.prepareStatement(
                "INSERT INTO messages (id, nonce, ciphertext, tag) VALUES (?, ?, ?, ?)")) {
            statement.setQueryTimeout(5);
            statement.setObject(1, id); statement.setBytes(2, nonce);
            statement.setBytes(3, Arrays.copyOf(encrypted, encrypted.length - 16));
            statement.setBytes(4, Arrays.copyOfRange(encrypted, encrypted.length - 16, encrypted.length));
            statement.executeUpdate();
        }
        respond(exchange, 201, Map.of("id", id.toString()));
    }
    static void get(HttpExchange exchange, String rawId) throws Exception {
        if (!rawId.matches("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")) {
            error(exchange, 400, "invalid_id"); return;
        }
        UUID id = UUID.fromString(rawId);
        byte[] nonce, ciphertext, tag;
        try (Connection connection = pool.getConnection(); PreparedStatement statement = connection.prepareStatement(
                "SELECT nonce, ciphertext, tag FROM messages WHERE id = ?")) {
            statement.setQueryTimeout(5); statement.setObject(1, id);
            try (ResultSet result = statement.executeQuery()) {
                if (!result.next()) { error(exchange, 404, "not_found"); return; }
                nonce = result.getBytes(1); ciphertext = result.getBytes(2); tag = result.getBytes(3);
            }
        }
        if (nonce.length != 12 || tag.length != 16) throw new IllegalArgumentException();
        byte[] encrypted = Arrays.copyOf(ciphertext, ciphertext.length + tag.length);
        System.arraycopy(tag, 0, encrypted, ciphertext.length, tag.length);
        String message = text(crypt(Cipher.DECRYPT_MODE, nonce, encrypted));
        respond(exchange, 200, Map.of("id", id.toString(), "message", message));
    }
    static void handle(HttpExchange exchange) {
        try {
            String path = exchange.getRequestURI().getPath();
            if (path.equals("/messages") && exchange.getRequestMethod().equals("POST")) post(exchange);
            else if (path.startsWith("/messages/") && path.indexOf('/', 10) < 0 && exchange.getRequestMethod().equals("GET"))
                get(exchange, path.substring(10));
            else error(exchange, 404, "not_found");
        } catch (Exception exception) {
            System.err.println("Request failed (database, cryptography or internal error)");
            if (exchange.getResponseCode() == -1) {
                try { error(exchange, 500, "internal_error"); } catch (IOException ignored) { }
            }
        } finally { exchange.close(); }
    }
    static HikariConfig databaseConfig() throws Exception {
        URI uri = URI.create(Objects.requireNonNull(System.getenv("DATABASE_URL")));
        if (!"postgresql".equals(uri.getScheme())) throw new IllegalArgumentException();
        String[] credentials = uri.getUserInfo().split(":", 2);
        String host = uri.getHost();
        if (host.indexOf(':') >= 0 && !host.startsWith("[")) host = "[" + host + "]";
        HikariConfig config = new HikariConfig();
        config.setJdbcUrl("jdbc:postgresql://" + host + ":" + (uri.getPort() < 0 ? 5432 : uri.getPort())
            + uri.getRawPath() + (uri.getRawQuery() == null ? "" : "?" + uri.getRawQuery()));
        config.setUsername(credentials[0]); config.setPassword(credentials.length > 1 ? credentials[1] : "");
        int maximum = Integer.parseInt(System.getenv().getOrDefault("DB_POOL_MAX", "10"));
        if (maximum < 1 || maximum > 10) throw new IllegalArgumentException();
        config.setMaximumPoolSize(maximum); config.setMinimumIdle(1); config.setAutoCommit(true);
        config.setConnectionTimeout(5000); config.setValidationTimeout(1000); config.setInitializationFailTimeout(1);
        config.addDataSourceProperty("connectTimeout", "5"); config.addDataSourceProperty("socketTimeout", "5");
        config.addDataSourceProperty("prepareThreshold", "0"); config.addDataSourceProperty("preparedStatementCacheQueries", "0");
        config.addDataSourceProperty("options", "-c statement_timeout=5000 -c synchronous_commit=on");
        return config;
    }
    public static void main(String[] args) {
        try {
            key = new SecretKeySpec(keyBytes(System.getenv("ENCRYPTION_KEY_BASE64")), "AES");
            pool = new HikariDataSource(databaseConfig());
            HttpServer server = HttpServer.create(new InetSocketAddress("0.0.0.0",
                Integer.parseInt(System.getenv().getOrDefault("PORT", "8080"))), 128);
            var executor = Executors.newVirtualThreadPerTaskExecutor();
            server.setExecutor(executor); server.createContext("/", Main::handle);
            Runtime.getRuntime().addShutdownHook(new Thread(() -> { server.stop(1); executor.close(); pool.close(); }));
            server.start();
        } catch (Exception exception) {
            System.err.println("Invalid startup configuration or database unavailable"); System.exit(1);
        }
    }
}
