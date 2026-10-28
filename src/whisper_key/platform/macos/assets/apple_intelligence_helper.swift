// apple_intelligence_helper.swift
// Tiny command-line bridge to Apple's on-device model (Foundation Models, macOS 26+).
// Python can't call the Swift-only framework, so platform/macos/apple_intelligence.py
// compiles this once with Apple's Command Line Tools and runs it per request.
//
//   apple_intelligence_helper availability   -> {"available": true} or {"available": false, "reason": "..."}
//   apple_intelligence_helper generate       stdin: {"prompt": "..."}  -> {"text": "..."} or {"error": "..."}
//
// One JSON object on stdout, always. The caller enforces the time limit by
// killing the process, which is the only reliable way to stop a stuck request.

import Foundation
import FoundationModels

// ── Output ───────────────────────────────────────────────────────────────────

// Writes one JSON object and exits; every path through the program ends here.
func finish(_ object: [String: Any], exitCode: Int32 = 0) -> Never {
    let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{\"error\":\"unknown\"}".utf8)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
    exit(exitCode)
}

// ── Availability ─────────────────────────────────────────────────────────────

// Stable reason codes the Python side turns into user-facing text.
func unavailableReasonCode(_ model: SystemLanguageModel) -> String? {
    switch model.availability {
    case .available: return nil
    case .unavailable(.appleIntelligenceNotEnabled): return "apple_intelligence_off"
    case .unavailable(.deviceNotEligible): return "device_not_eligible"
    case .unavailable(.modelNotReady): return "model_not_ready"
    case .unavailable(_): return "unknown"
    }
}

// ── Generation ───────────────────────────────────────────────────────────────

// Maps the framework's errors to short codes; "guardrail" and "too_long" matter to the caller.
func generationErrorCode(_ error: Error) -> String {
    guard let generationError = error as? LanguageModelSession.GenerationError else { return "failed" }
    switch generationError {
    case .exceededContextWindowSize: return "too_long"
    case .guardrailViolation, .refusal: return "guardrail"
    case .unsupportedLanguageOrLocale: return "unsupported_language"
    case .assetsUnavailable: return "model_not_ready"
    case .rateLimited, .concurrentRequests: return "busy"
    default: return "failed"
    }
}

// Permissive guardrails: we only ever transform the user's own text, and the
// default guardrails reject harmless dictations (the POC saw a nursery rhyme
// blocked). Greedy sampling keeps polish deterministic.
func generate(prompt: String) async -> Never {
    let model = SystemLanguageModel(guardrails: .permissiveContentTransformations)
    if let reason = unavailableReasonCode(model) {
        finish(["error": "unavailable", "reason": reason], exitCode: 2)
    }
    do {
        let session = LanguageModelSession(model: model)
        var options = GenerationOptions()
        options.sampling = .greedy
        let response = try await session.respond(to: prompt, options: options)
        finish(["text": response.content])
    } catch {
        finish(["error": generationErrorCode(error), "detail": "\(error)"], exitCode: 1)
    }
}

// ── Entry ────────────────────────────────────────────────────────────────────

let arguments = CommandLine.arguments
switch arguments.count > 1 ? arguments[1] : "" {
case "availability":
    if let reason = unavailableReasonCode(SystemLanguageModel.default) {
        finish(["available": false, "reason": reason])
    }
    finish(["available": true])
case "generate":
    let input = FileHandle.standardInput.readDataToEndOfFile()
    guard let request = try? JSONSerialization.jsonObject(with: input) as? [String: Any],
          let prompt = request["prompt"] as? String, !prompt.isEmpty else {
        finish(["error": "bad_request"], exitCode: 64)
    }
    await generate(prompt: prompt)
default:
    finish(["error": "bad_request", "detail": "usage: availability | generate"], exitCode: 64)
}
