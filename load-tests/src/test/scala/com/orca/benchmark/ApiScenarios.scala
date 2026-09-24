package com.orca.benchmark

import io.gatling.core.Predef._
import io.gatling.http.Predef._
import scala.concurrent.duration._

/** Shared request definitions. Injection profiles live in the two simulations. */
object ApiScenarios {
  val baseUrl = System.getProperty("baseUrl", "http://api-go:8080")
  val payloadBytes = Integer.getInteger("payloadBytes", 128)
  val seed = System.getProperty("seed", "benchmark")
  val message = deterministicMessage(payloadBytes, seed)
  private def escapeJson(value: String): String = value.flatMap {
    case '"' => "\\\""
    case '\\' => "\\\\"
    case '\n' => "\\n"
    case '\r' => "\\r"
    case '\t' => "\\t"
    case c if c < ' ' => f"\\u${c.toInt}%04x"
    case c => c.toString
  }
  val messageJson = s"{\"message\":\"${escapeJson(message)}\"}"

  private def deterministicMessage(size: Int, seed: String): String = {
    require(Set(128, 1024, 16384).contains(size), s"payloadBytes must be 128, 1024 or 16384, got $size")
    val unit = s"ação-日本語-😀|$seed"
    val builder = new StringBuilder
    while ((builder.toString + unit).getBytes("UTF-8").length <= size) builder.append(unit)
    while (builder.toString.getBytes("UTF-8").length < size) builder.append("x")
    builder.toString
  }

  val httpProtocol = http
    .baseUrl(baseUrl)
    .acceptHeader("application/json")
    .contentTypeHeader("application/json")
    .userAgentHeader("orca-benchmark-gatling/1.0")
    .disableCaching
    .shareConnections

  val post = exec(
    http("POST /messages")
      .post("/messages")
      .body(StringBody(messageJson)).asJson
      .requestTimeout(5.seconds)
      .check(status.is(201))
      .check(jsonPath("$.id").ofType[String].saveAs("createdId"))
  )

  def get = feed(csv(System.getProperty("idsFile", "data/ids.csv")).random).exec(
    http("GET /messages")
      .get("/messages/#{id}")
      .requestTimeout(5.seconds)
      .check(status.is(200))
      .check(jsonPath("$.id").exists)
      .check(jsonPath("$.message").exists)
  )

  val roundTrip = exec(
    http("POST /messages")
      .post("/messages")
      .body(StringBody(messageJson)).asJson
      .requestTimeout(5.seconds)
      .check(status.is(201))
      .check(jsonPath("$.id").saveAs("createdId"))
  ).exec(
    http("GET /messages")
      .get("/messages/#{createdId}")
      .requestTimeout(5.seconds)
      .check(status.is(200))
      .check(jsonPath("$.message").exists)
  )
}
