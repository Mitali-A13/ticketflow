# TicketFlow evaluation results

Run on Nov 13, 2026 with `python -m eval.run_eval --split test --label final`.
Model: openai/gpt-oss-120b (Groq), prompt v2, confidence threshold 0.7,
reasoning_effort low, temperature 0. Code at commit 050dc54 (the freeze commit).
Raw per-email results: `eval/runs/test-final-20261113-122050.json`.
The test set was run once. Settings and success criteria were frozen in
`eval/PLAN.md` before the run and were not changed afterwards.

## Data
40 synthetic emails (10 dev, 30 test), drafted with an LLM assistant. Every gold
label was reviewed by hand by one person (the author). Two labels were changed in
that review: e19 and e30 (both test emails), urgency from medium to high. A
comparison of the final dataset with the original LLM draft found no other
difference in any label; all 10 dev labels are unchanged.

The review happened before any model run, and before the first commit (d3f0ec9),
so git shows no diff for these edits; the disclosure in PLAN.md and this file is
the only record. Had the two draft labels been kept, test urgency would be 30/30.
Category, customer_name and order_id scores and all four success criteria do not
depend on urgency. The author also read all test emails while reviewing the
labels, before any tuning.

Dev set: 10 emails, used only to choose between two prompts.

## History (git)
- d3f0ec9: first commit. Contained 39 of 40 emails (a copy error), the reviewed
  labels and PLAN.md.
- dc27d93: added the missing email (e15) and the label-review disclosure.
- d3421a4: evaluation tooling (scoring, baseline, runner), before any model run.
- dd9dc69: dev run with prompt v1 (category 9/10, urgency 8/10).
- 6f1034f: prompt v2 adopted (dev category 10/10, urgency 9/10), under an adoption rule agreed before the run (at least 17/20, the v1 score; git records the rule only from 050dc54).
- 050dc54: settings frozen in PLAN.md. The test run used this commit.
- 9b37b52: recorded the final test run (raw JSON) and this file.
- 70fc6d7: PLAN.md disclosure of the label changes.

PLAN.md was edited after the test run, in disclosure text only: the date in its
header, the Data section and the tuning log. Its Labeling rules, Metrics and
Success criteria sections are identical in commits dc27d93, 050dc54 and the latest
commit (checked by hashing those sections).

## Pre-registered success criteria
| Criterion | Result |
|---|---|
| A: LLM category accuracy at least 85% | PASS: 30/30 |
| B: beats the keyword baseline by at least 10 points | PASS: 30/30 vs 21/30 |
| C: auto-accepted category accuracy strictly higher than overall | FAIL: 23/23 vs 30/30 |
| D: at least 29 of 30 emails with a valid result | PASS: 30/30 |

Three of four criteria were met. Criterion C failed as written. The reason is a
flaw in how it was designed, not evidence about routing: with 100% category
accuracy overall, auto-accepted accuracy cannot be strictly higher. There were no
category errors for review routing to catch, so these data cannot say whether
routing helps for category.

## Metrics (test set, 30 emails)
- Category 30/30 (100%), urgency 28/30 (93.3%), customer_name 30/30,
  order_id 30/30 (exact match; a missing result would count as wrong).
- Hallucination on the raw model output: order_id 0/30, customer_name 0/30.
  After the code's literal-match check, the order_id rate is 0 by construction.
- Auto-accepted: 23/30 (76.7%); the other 7 went to human review.
- Valid structured output on the first attempt: 30/30. No retries and no dead
  jobs. Mean attempts per job 1.0. Median LLM latency 999.5 ms (the model call
  only, from the pipeline's own timer).
- Keyword baseline (category only): 21/30 (70.0%).
- Injection: 2 test emails had marker strings that would appear only if the model
  obeyed the injected instruction; neither appeared. Hand reading of all three
  test injection emails (e32, e33, e34): none obeyed. Each output was valid JSON
  with the correct category, urgency, customer name and order id, no invented
  order id (ORD-00001 was not returned), no "Arr" reply and no system prompt in
  the summary. Three crude attacks against one model and one prompt: this is not
  a security guarantee.

## Failure analysis

### LLM errors (2 of 30 emails, both urgency)
| Email | Tags | Gold | Model | Status | Confidence |
|---|---|---|---|---|---|
| e19 | no order id | high | medium | auto-accepted | 0.98 |
| e30 | rambling | high | medium | auto-accepted | 0.98 |

- Neither email states a deadline. The author raised both gold labels from the
  drafted medium to high during review, reading them as "clearly time-critical":
  e19 asks to change the delivery address on a pending order, and e30 reports a
  possible unauthorised charge on the customer's card. The model chose medium for
  both. The urgency rule allows either reading, so these two are as much
  label-rule ambiguity as model errors. They are counted as errors as scored.
- Both were confident and went through auto-accept. Of the 23 auto-accepted
  emails, 21 had the right urgency (91.3%); in the review queue 7 of 7 did.
  The routing rules did not catch urgency errors, and confidence never separated
  right answers from wrong ones in this data.
- No category, customer_name or order_id errors on the test set, including the
  5 non-English emails, the 3 injection emails and the 4 multi-issue emails.

### Keyword baseline errors (9 of 30, category)
- Non-English (4 of the 5 non-English emails): e36, e37, e38, e39. English
  keyword rules cannot read Spanish, French, German or Hindi.
- Account emails read as billing (2): e15, e24.
- Keywords leaking into "other" emails (2): e29, e40.
- Multi-issue (1): e25.

## Limitations
- 30 test emails is a small sample: one email is 3.3 points. A result of 30/30
  is consistent with a true accuracy as low as about 88% (exact two-sided 95%
  lower bound for 30 of 30: 0.025^(1/30) = 88.4%), and even that assumes a random
  sample of real emails.
- The emails are synthetic: shorter, cleaner and more regular than real support
  mail. Only 5 of the 30 test emails are non-English.
- Labels were drafted by an LLM assistant and reviewed by one person. The labeling
  rules, the labels and the v2 prompt share one source, so high agreement partly
  reflects shared conventions, not real-world difficulty.
- The prompt was tuned on only 10 dev emails, and v2 was written after seeing v1's
  dev errors. Dev scores are therefore optimistic. Only this test run is a fair
  estimate. The author read the test emails before tuning.
- Exact-match scoring does not measure summary or reply quality. Drafted replies
  can contain placeholders such as "[Your Name]", promise actions that have not
  happened (one reply offers to "arrange a refund"), and occasionally drop a space
  between words.
- One run, one model, no variance estimate. Retries, malformed output and dead
  jobs never occurred with the real model in this evaluation. Those paths are
  covered only by unit tests.
- The keyword baseline was written after the author had seen all emails, and it
  is English-only.