package com.orca.benchmark

import io.gatling.core.Predef._
import scala.concurrent.duration._

class ClosedWorkloadSimulation extends Simulation {
  private val phase = System.getProperty("phase", "measure")
  private val scenarioName = System.getProperty("scenario", "get")
  private val users = Integer.getInteger("users", 1)
  private val duration = Integer.getInteger("durationSeconds", 120).seconds

  private val action = scenarioName match {
    case "post" => ApiScenarios.post
    case "get" => ApiScenarios.get
    case "mixed" => randomSwitch(50.0 -> ApiScenarios.post, 50.0 -> ApiScenarios.get)
    case "roundtrip" => ApiScenarios.roundTrip
    case other => throw new IllegalArgumentException(s"Unknown scenario: $other")
  }

  // The scenario duration bounds the loop; an unbounded forever loop would
  // outlive the closed injection step and make the run impossible to drain.
  private val deadline = new java.util.concurrent.atomic.AtomicLong(0L)
  private val workload = scenario(s"closed-$scenarioName-$phase")
    .exec { session =>
      deadline.compareAndSet(0L, System.currentTimeMillis() + duration.toMillis)
      session
    }
    .asLongAs(_ => System.currentTimeMillis() < deadline.get(), exitASAP = false)(exec(action))

  // Scala DSL uses inject; constantConcurrentUsers is the closed workload step.
  setUp(workload.inject(constantConcurrentUsers(users).during(duration)))
    .protocols(ApiScenarios.httpProtocol)
    .maxDuration(duration + 10.seconds)
    .assertions(global.failedRequests.percent.lte(100.0))
}
