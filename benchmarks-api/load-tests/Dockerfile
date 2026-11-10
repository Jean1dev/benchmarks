FROM maven:3.9.11-eclipse-temurin-17@sha256:e4a7ace3dc0d645ed97f8d9ad0b0d3f0b14fa8d150138f27f116d7105a639b82 AS build
WORKDIR /workspace
COPY pom.xml ./
RUN mvn -B -ntp dependency:go-offline
COPY src ./src
RUN mvn -B -ntp -DskipTests test-compile

FROM maven:3.9.11-eclipse-temurin-17@sha256:e4a7ace3dc0d645ed97f8d9ad0b0d3f0b14fa8d150138f27f116d7105a639b82
WORKDIR /workspace
COPY --from=build /root/.m2 /root/.m2
COPY --from=build /workspace/pom.xml /workspace/pom.xml
COPY --from=build /workspace/src /workspace/src
COPY --from=build /workspace/target /workspace/target
ENV MAVEN_OPTS="-Xms256m -Xmx1024m -XX:+UseSerialGC -XX:ActiveProcessorCount=2"
ENTRYPOINT ["mvn", "-B", "-ntp", "gatling:test"]
