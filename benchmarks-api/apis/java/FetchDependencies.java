import java.net.URI;
import java.nio.file.*;
public class FetchDependencies {
    public static void main(String[] args) throws Exception {
        Files.createDirectories(Path.of("lib"));
        for (String artifact : Files.readAllLines(Path.of("dependencies.txt"))) {
            try (var input = URI.create("https://repo.maven.apache.org/maven2/" + artifact).toURL().openStream()) {
                Files.copy(input, Path.of("lib", artifact.substring(artifact.lastIndexOf('/') + 1)));
            }
        }
    }
}
