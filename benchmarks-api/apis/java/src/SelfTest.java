import javax.crypto.Cipher;
import javax.crypto.AEADBadTagException;
import javax.crypto.spec.SecretKeySpec;
import java.nio.charset.CharacterCodingException;
import java.util.*;

public class SelfTest {
    public static void main(String[] args) throws Exception {
        byte[] key = new byte[32]; Main.RANDOM.nextBytes(key);
        Main.key = new SecretKeySpec(Main.keyBytes(Base64.getEncoder().encodeToString(key)), "AES");
        for (String message : List.of("", "Olá 🌊 \\ \n", "a".repeat(16384))) {
            byte[] nonce = new byte[12]; Main.RANDOM.nextBytes(nonce);
            byte[] ciphertext = Main.crypt(Cipher.ENCRYPT_MODE, nonce, Main.utf8(message));
            if (!message.equals(Main.text(Main.crypt(Cipher.DECRYPT_MODE, nonce, ciphertext))))
                throw new AssertionError("Round-trip failed");
            ciphertext[ciphertext.length - 1] ^= 1;
            try { Main.crypt(Cipher.DECRYPT_MODE, nonce, ciphertext); throw new AssertionError("Tampering accepted"); }
            catch (AEADBadTagException expected) { }
        }
        for (String invalid : List.of("", "!!!", "YWJj")) {
            try { Main.keyBytes(invalid); throw new AssertionError("Invalid key accepted"); }
            catch (IllegalArgumentException expected) { }
        }
        try { Main.text(new byte[]{(byte)0xff}); throw new AssertionError("Invalid UTF-8 accepted"); }
        catch (CharacterCodingException expected) { }
        try { Main.utf8("\ud800"); throw new AssertionError("Unpaired surrogate accepted"); }
        catch (CharacterCodingException expected) { }
        System.out.println("SelfTest passed: round-trip, authentication, key and UTF-8 validation");
    }
}
