# Whisper Local - Roadmap
@completed.md

## Next
- As a *user* I want **"learn from my fix"** so that when I correct a dictation in place, one hotkey compares my edit with the typed text and offers to save the change as a replacement or a word (Wispr Flow's dictionary auto-learn, done offline)
- As a *developer* I want **spoken casing commands** ("camel case foo bar" → `fooBar`, "snake case …") for dictating identifiers
- As a *user* I want **Command Mode without a selection**: "search Perplexity for …" opens a search, "write …" drafts a text with the AI provider

## Bugs
- **CUDA version forward-compatibility** - onboarding GPU detection doesn't recognize newer CUDA versions (e.g. CUDA 13.x), and CTranslate2 requires `cublas64_12.dll` which isn't present when only CUDA 13+ is installed. Workaround: manually copy CUDA 12 cuBLAS/cuDNN libs into CUDA 13 bin directory. Need to detect newer CUDA versions and guide users to install the required CUDA 12 compatibility libs
- **Ctrl+C doesn't work after HuggingFace download** - shutdown signal not caught
- **(macOS) System freezes on transcription** - needs verification
- **(macOS) Stop hotkey requires double-press** - remapping stop hotkey to Ctrl requires pressing it twice to stop recording. Default Fn key also conflicts with macOS language keyboard toggle
- **GPU model switch crash** - upstream CT2 bug, `thread_local` GPU handles corrupt HIP/CUDA on Worker thread teardown ([faster-whisper #71](https://github.com/SYSTRAN/faster-whisper/issues/71)). Future option: auto-restart app on model switch in GPU mode

## Backlog

### Voice Commands
- As a *user* I want **command aliases** so I can define multiple trigger phrases for the same command (e.g., "open browser", "launch chrome", and "start browser" all execute the same command). Today only `match_regex` can do this
- As a *user* I want **semantic command matching** so commands are matched intelligently instead of requiring exact phrases (e.g., "open up chrome" still matches "open browser")
- As a *user* I want **parameterized commands** so I can define command templates with variables that are extracted from speech (e.g., "open {filename}" or "search for {query}")
- As a *user* I want **voice commands API** so other apps can do things from whisper key commands
- As a *user* I want **verbal stop recording** command so I don't need to hit the hotkey

### Delivery
- As a *user* I want **auto-set key simulation delay based on machine specs** so slower computers don't miss keystrokes during auto-paste

### Hotkeys
- As a *user* I want **hotkey sequence ordering** so more hotkey combos can coexist without conflicting (e.g., Ctrl+Alt+Space vs Alt+Ctrl+Space become distinct sequences)
- As a *developer* I want a **typed `HotkeyBinding` contract** between `hotkey_listener.py` and the platform backends so library-specific fields stop leaking across the boundary

### GPU Onboarding
- As a *user* I want **guided GPU setup** so drivers and dependencies are auto-detected, downloaded, and installed through an in-app UI instead of manual steps

### Packaging & Updates
- As a *user*, I want **config version tracking with auto-reset on breaking changes** so my settings don't cause errors after major updates

### macOS
- As a *mac user*, I want **pyapp build for macOS** so Mac users get the same single-binary packaging experience as Windows
  - As a *mac user*, I want **`restart_or_exit` to work on macOS** so auto-update and GPU onboarding restart correctly (`CREATE_NEW_CONSOLE` is Windows-only)
- As a *mac user*, I want **monochrome menu bar icons** so the app conforms to Apple's template icon guidelines

### Cross-Platform
- As a *Linux user* I want **Linux desktop support** so I can use Whisper Local on my Linux machine — add `platform/linux/` with evdev-based global hotkeys and key simulation
  - As a *Linux user* I want **global hotkeys via evdev** so hotkey detection works on both X11 and Wayland
  - As a *Linux user* I want **key simulation via evdev UInput** so auto-paste works on both X11 and Wayland
  - As a *Linux user* I want **input group permission guidance** so I get clear setup instructions when evdev access is missing
  - As a *Linux user* I want **Linux GPU transcription** so I can use CUDA or ROCm (which works better on Linux than Windows)
- As a *WSL user* I want **WSL support via a Windows hotkey shim** so I can run the app natively in WSL while capturing global hotkeys on the Windows side
- As a phone *user*, I want this app on **mobile**

### Remote Transcription
- As a *user* I want **remote transcription providers** so I can use cloud or self-hosted APIs instead of local processing
  - As a *user* I want **OpenAI Whisper API** so I can use OpenAI's cloud transcription
  - As a *user* I want **Groq Whisper API** so I can get fast cloud transcription at low cost
  - As a *user* I want **Mistral Voxtral API** so I can use Voxtral Transcribe 2 with diarization
  - As a *user* I want **xAI Grok API** so I can use Grok's transcription service

### Developer Use Cases
- As a *developer* I want **project file context** so I can reference files in voice and tag them in chats hands free

### Agent Integration
- As a *user* I want a **CLI interface** so other tools and agents can invoke transcription programmatically
- As a *user* I want to **wrap CLI tools** (e.g., `wl claude`) so I can voice-input directly into any command-line application without switching windows
  - As a *user* I want **additional instances with custom hotkeys** so that I can be keyboard-only when dictating to multiple agents
  - As a *user* I want **hotkey instance management** so that multiple instances will be allowed and work together
- As a *user* I want **agent skill packages** so CLI agents (Claude Code, Codex, OpenClaw, etc.) can install a whisper-local skill from a marketplace and use the CLI
- As a *user* I want to **send transcriptions to CLI agents** (Claude Code, Codex, etc.) so I can voice-control coding assistants
- As a *user* I want **Telegram bot integration** so I can transcribe voice messages for bots like OpenClaw

### Discord Integration
- As a *user* I want to **transcribe and post commands to CLI agent at home** so I can control my home server remotely via Discord
- As a *user* I want a **Discord bot for voice commands** so I can trigger actions remotely with my voice
- As a *user* I want to **listen for commands from multiple users** so a shared server can accept voice input from authorized people
