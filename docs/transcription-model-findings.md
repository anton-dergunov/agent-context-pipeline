# Multilingual transcription benchmark

Benchmark date: 2026-08-09/10. Machine: Apple Silicon Mac, CPU `int8`, one
inference thread, beam size 5, VAD enabled. The suite contained 40 controlled
FLEURS recordings (ten each in English, Spanish, Russian, and Mandarin), all
nine downloaded Instagram videos, and two Telegram voice messages.

No production model has been selected and no model has been added to Docker.

## Resource results

The primary input set contained about 14.3 minutes of audio. “Real-time” is
inference time divided by audio duration; below 1.0 processes faster than the
audio plays. The suggested allowance is 1.5 times measured peak RSS rounded to
256 MB.

| Model | Model files | Peak RSS | Suggested allowance | Primary inference | Real-time |
|---|---:|---:|---:|---:|---:|
| tiny | 78 MB | 542 MB | 1,024 MB | 39.2 s | 0.05x |
| base | 148 MB | 849 MB | 1,280 MB | 75.6 s | 0.09x |
| small | 486 MB | 1,347 MB | 2,048 MB | 237.5 s | 0.28x |
| medium | 1,531 MB | 2,753 MB | 4,352 MB | 707.2 s | 0.82x |
| large-v3 | 3,091 MB | 3,985 MB | 6,144 MB | 1,597.4 s | 1.86x |
| turbo | 1,622 MB | 1,843 MB | 2,816 MB | 1,152.0 s | 1.34x |

The numbers measure this Mac, not the Ryzen R1600 NAS or Raspberry Pi. They are
useful for relative comparison, while target-machine throughput should be
measured again after a model is selected.

## Reference quality by language

Macro-average ROUGE-L F1 against human FLEURS transcripts after Unicode,
punctuation, case, and whitespace normalization. Mandarin uses word
segmentation rather than raw characters.

| Model | English | Spanish | Russian | Mandarin | Four-language mean |
|---|---:|---:|---:|---:|---:|
| tiny | 91.6% | 91.6% | 76.4% | 50.0% | 77.4% |
| base | 93.3% | 94.2% | 86.9% | 55.1% | 82.4% |
| small | 96.8% | 97.7% | 91.2% | 68.5% | 88.6% |
| medium | 97.4% | 98.7% | 93.3% | 93.3% | 95.7% |
| large-v3 | 97.2% | 98.2% | 98.0% | 97.2% | 97.7% |
| turbo | 96.8% | 98.7% | 97.8% | 89.9% | 95.8% |

Every model detected the expected language on all 40 controlled recordings.
The larger differences are transcription quality, particularly Russian and
Mandarin, rather than language identification.

## Practical observations

- `small` is already close to the best models for English and Spanish, but has
  a large Mandarin deficit and a noticeable Russian deficit.
- `medium` is the strongest balanced compromise in this run: near-best English
  and Spanish, good Mandarin, faster than real time, and a 2.75 GB working set.
- `large-v3` gives the best Russian and Mandarin results, but took 26.6 minutes
  for 14.3 minutes of primary audio and was especially slow on music.
- `turbo` nearly matches large-v3 in Russian, matches medium in Spanish, uses
  less memory than medium, but loses Mandarin quality and was slower than
  medium on this one-thread CPU run.
- VAD produced empty text for every expected instrumental/music-only post for
  all six models. With VAD disabled, every model hallucinated on at least one
  of those posts; examples included repeated “Thank you”, invented music
  titles, and meaningless syllables. VAD should remain enabled.
- Both carousel videos without audio were classified as `no_audio` rather than
  failures.
- On the first Telegram message, `tiny` wrote “boat” instead of “bot”; base and
  every larger model produced “Voice message for testing how it works in the
  bot.” All models correctly transcribed the second Telegram message. Turbo
  assigned the highest language confidence to both.

The complete generated report, including ROUGE-L precision/recall, Jaccard,
large-v3 agreement, VAD-on/off text, and exact side-by-side Instagram/Telegram
transcripts, is available locally at `.bench_transcription/report.md`.

