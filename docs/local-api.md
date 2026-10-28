# Local OpenAI-compatible API

`whisper-local --serve` starts a small web server on your computer. It accepts audio and returns text, in the same shape as the OpenAI Whisper API. Tools that speak that API can use your local model. No data leaves your machine. [Back to the README](../README.md).

## Start the server

```bash
whisper-local --serve                      # http://127.0.0.1:7777
whisper-local --serve --serve-port 8080    # another port
```

The server loads your configured speech model once, then answers requests. Press Ctrl+C to stop it. It uses the same model and settings as dictation, and needs no API key.

By default the server listens on `127.0.0.1`, so only programs on your computer can reach it. `--serve-host` changes this. The server has no login. Do not bind it to a public address.

## Endpoints

| Method and path | What it does |
|---|---|
| `POST /v1/audio/transcriptions` | Transcribes an uploaded audio file |
| `GET /v1/models` | Lists one model, `whisper-1` |
| `GET /health` | Returns `{"status": "ok"}` |

### Transcription request

Send `multipart/form-data` with these fields.

| Field | Required | Notes |
|---|---|---|
| `file` | yes | The audio. WAV works everywhere. Other formats (FLAC, OGG) work when the `soundfile` package is present. |
| `response_format` | no | `json` (default) returns `{"text": "..."}`. `text` returns plain text. |
| `language` | no | A language code such as `en`. `auto` or empty uses your setting. |
| `prompt` | no | A hint for the model. |
| `model` | no | Accepted, but ignored. The server always uses your configured model. |

The largest upload is 500 MB. The server handles one transcription at a time.

```bash
curl -X POST http://127.0.0.1:7777/v1/audio/transcriptions \
  -F file=@audio.wav -F model=whisper-1 -F response_format=text
```

## Use it with other tools

Point any tool that speaks the OpenAI audio API at `http://127.0.0.1:7777/v1`. Give it any text as the API key. For example, with the OpenAI Python SDK:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:7777/v1", api_key="unused")
with open("audio.wav", "rb") as audio_file:
    result = client.audio.transcriptions.create(model="whisper-1", file=audio_file)
print(result.text)
```

Related: [Models](models.md), [Privacy](privacy.md).
