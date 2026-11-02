// apple_intelligence_helper.swift
// Tiny command-line bridge to Apple's on-device model (Foundation Models, macOS 26+).
// Python can't call the Swift-only framework, so platform/macos/apple_intelligence.py
// compiles this once with Apple's Command Line Tools and keeps one `serve` process
// running, so the model stays warm between dictations.
//
//   apple_intelligence_helper availability   -> {"available": true} or {"available": false, "reason": "..."}
//   apple_intelligence_helper serve          one JSON request per stdin line, until stdin closes:
//       {"id": 1, "op": "prewarm"}                  -> no reply (loads the model ahead of a request)
//       {"id": 2, "op": "generate", "prompt": "..."} -> {"id": 2, "text": "..."} or {"id": 2, "error": "..."}
//
// The caller enforces the time limit by killing the process, which is the only
// reliable way to stop a stuck request; it then starts a fresh one.

import Foundation
import FoundationModels

// ── Output ───────────────────────────────────────────────────────────────────

// Writes one JSON object as one line. FileHandle writes are unbuffered, so the
// caller sees the reply at once.
func reply(_ object: [String: Any]) {
    let data = (try? JSONSerialization.data(withJSONObject: object)) ?? Data("{\"error\":\"unknown\"}".utf8)
    FileHandle.standardOutput.write(data + Data("\n".utf8))
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

// Answers requests until stdin closes (the app quit or crashed), so the helper
// never outlives the app. Every generate gets a NEW session: a session keeps its
// transcript, so reusing one would leak earlier dictations into later prompts.
// Permissive guardrails: we only ever transform the user's own text, and the
// default guardrails reject harmless dictations (the POC saw a nursery rhyme
// blocked). Greedy sampling keeps polish deterministic.
func serve() async {
    let model = SystemLanguageModel(guardrails: .permissiveContentTransformations)
    // A session prewarmed for the next generate; nil when none is waiting.
    var prewarmedSession: LanguageModelSession? = nil

    do {
        for try await line in FileHandle.standardInput.bytes.lines {
            guard let request = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] else {
                reply(["error": "bad_request"])
                continue
            }
            let requestId = request["id"] ?? NSNull()

            switch request["op"] as? String {
            case "prewarm":
                if unavailableReasonCode(model) == nil {
                    let session = LanguageModelSession(model: model)
                    session.prewarm()
                    prewarmedSession = session
                }

            case "generate":
                guard let prompt = request["prompt"] as? String, !prompt.isEmpty else {
                    reply(["id": requestId, "error": "bad_request"])
                    continue
                }
                if let reason = unavailableReasonCode(model) {
                    reply(["id": requestId, "error": "unavailable", "reason": reason])
                    continue
                }
                let session = prewarmedSession ?? LanguageModelSession(model: model)
                prewarmedSession = nil
                do {
                    var options = GenerationOptions()
                    options.sampling = .greedy
                    let response = try await session.respond(to: prompt, options: options)
                    reply(["id": requestId, "text": response.content])
                } catch {
                    reply(["id": requestId, "error": generationErrorCode(error), "detail": "\(error)"])
                }

            default:
                reply(["id": requestId, "error": "bad_request", "detail": "op must be prewarm or generate"])
            }
        }
    } catch {
        // stdin broke: nothing more to answer.
    }
}

// ── Entry ────────────────────────────────────────────────────────────────────

let arguments = CommandLine.arguments
switch arguments.count > 1 ? arguments[1] : "" {
case "availability":
    if let reason = unavailableReasonCode(SystemLanguageModel.default) {
        reply(["available": false, "reason": reason])
    } else {
        reply(["available": true])
    }
case "serve":
    await serve()
default:
    reply(["error": "bad_request", "detail": "usage: availability | serve"])
    exit(64)
}
