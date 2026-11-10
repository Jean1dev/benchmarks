FROM eclipse-temurin:25.0.2_10-jdk-noble AS build
WORKDIR /build
COPY dependencies.txt dependencies.sha256 FetchDependencies.java ./
RUN java FetchDependencies.java && sha256sum -c dependencies.sha256
COPY src ./src
RUN mkdir classes && javac --release 25 -encoding UTF-8 -cp 'lib/*' -d classes src/*.java
FROM eclipse-temurin:25.0.2_10-jre-noble
WORKDIR /app
COPY --from=build /build/lib ./lib
COPY --from=build /build/classes ./classes
USER 65534:65534
ENV PORT=8080
EXPOSE 8080
CMD ["java", "-Xms16m", "-Xmx128m", "-XX:+UseSerialGC", "-XX:ActiveProcessorCount=1", "-XX:ReservedCodeCacheSize=32m", "-XX:MaxDirectMemorySize=16m", "-Dsun.net.httpserver.maxReqTime=5", "-Dsun.net.httpserver.maxRspTime=10", "-Dsun.net.httpserver.idleInterval=60", "-cp", "classes:lib/*", "Main"]
