package com.orca.benchmark

import io.gatling.core.Predef._
import scala.concurrent.duration._

class OpenWorkloadSimulation extends Simulation {
  private val phase = System.getProperty("phase", "measure")
  private val scenarioName = System.getProperty("scenario", "get")
  private val rate = java.lang.Double.parseDouble(System.getProperty("ratePerSecond", "10"))
  private val duration = Integer.getInteger("durationSeconds", 120).seconds

  private val action = scenarioName match {
    case "post" => ApiScenarios.post
    case "get" => ApiScenarios.get
    case "mixed" => randomSwitch(50.0 -> ApiScenarios.post, 50.0 -> ApiScenarios.get)
    case "roundtrip" => ApiScenarios.roundTrip
    case other => throw new IllegalArgumentException(s"Unknown scenario: $other")
  }

  // One virtual user makes exactly one request. This remains an open model.
  private val workload = scenario(s"open-$scenarioName-$phase").exec(action)

  // Scala DSL uses inject; constantUsersPerSec is the open workload step.
  setUp(workload.inject(constantUsersPerSec(rate).during(duration)))
    .protocols(ApiScenarios.httpProtocol)
    .maxDuration(duration + 10.seconds)
    .assertions(global.failedRequests.percent.lte(100.0))
}
