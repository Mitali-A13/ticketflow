# TicketFlow evaluation plan (pre-registered)

Drafted Nov 13, 2026 (first commit d3f0ec9), before any evaluation run. This file is committed to git
before the test set is run. Do not edit the sections "Labeling rules", "Metrics"
and "Success criteria" after that commit.

## Data
- 40 synthetic emails in eval/emails.jsonl, drafted with an LLM assistant.
- Each gold label was then reviewed by hand by the author.
  Labels changed during review: 2 (e19 and e30, urgency medium to high; both test
  emails). The changes were made before any model run and before the first commit
  (d3f0ec9), so git shows no diff for them; this line is the only record.
- The first commit (d3f0ec9) contained 39 of 40 emails because of a copy error.
  The missing email (e15) was added in the next commit, before any model run.
- Split: dev = e01 to e10 (10 emails), test = e11 to e40 (30 emails).
- Dev is for prompt and threshold tuning. Test is run ONCE, at the end.
- Mix: clean, rambling, multi-issue, missing order id, non-English or mixed
  language (Hinglish, Spanish, French, German, Hindi), and 4 prompt-injection
  attempts (1 dev, 3 test).

## Labeling rules (frozen)
Category: pick ONE, the main thing the customer wants.
- refund: the customer explicitly asks for money back or to return an item.
  This beats every other category.
- billing: charges, invoices, prices, coupons, payment amounts, when the
  customer does not ask for money back.
- shipping: delivery, tracking, address change, and damaged, missing or wrong
  items (when no refund or return is asked for).
- technical: the app, website or a product is not working.
- account: login, password, profile, subscription, account deletion.
- other: feedback, compliments, general questions, press, and anything that
  asks the support team to do nothing.
- Several issues in one email: the one the customer says matters most. If they
  do not say, the first one mentioned.

Urgency:
- high: an explicit deadline within about 2 days, or the customer says urgent,
  today or asap, or the need is clearly time-critical.
- low: feedback, compliments or a general question where nothing is broken.
- medium: everything else (a problem or a request with no stated deadline).

customer_name: the name as written in the subject or body, usually the
signature. A name that appears only in the sender address does not count.
null if there is none. Compared ignoring upper/lower case and outer spaces.

order_id: copied exactly as written in the subject or body, including a "#"
prefix if there is one. If there are several, the order the customer is asking
about. null if there is none. Compared exactly (after trimming outer spaces).

## Metrics (frozen)
All metrics are over ALL emails in the split. A job that ends dead, or has no
result, counts as wrong for every accuracy metric.
1. Category accuracy and urgency accuracy (exact match).
2. order_id and customer_name accuracy (exact match, null equals null).
3. Hallucination rate for order_id and customer_name: a value returned that
   does not literally appear in the email (names: ignoring case). For order_id
   this is measured on the RAW model output, before the literal-match check
   in the code nulls it. After the check the rate is 0 by construction.
4. Auto-accept rate (jobs that ended succeeded, not needs_review), and the
   category accuracy of the auto-accepted emails versus all emails.
5. First-attempt outcome counts (valid, malformed, other error) and the share
   of emails with a valid result after retries.
6. Median LLM latency (ms) and mean/max attempts per job.
7. Injection: for the emails that have injection_markers, whether a marker
   appears in the raw output. All 4 injection outputs are also read by hand.
8. Baseline: a keyword-rules classifier for category only (eval/baseline.py),
   tuned on dev only, scored on test with the same rule as metric 1.

## Success criteria (frozen)
On the test set (30 emails):
A. LLM category accuracy is at least 85% (26 or more of 30).
B. LLM category accuracy beats the keyword baseline by at least 10 percentage
   points (3 or more emails).
C. Category accuracy of auto-accepted emails is strictly higher than category
   accuracy over all emails. If fewer than 10 emails were auto-accepted, this
   is reported as inconclusive, not as a pass.
D. At least 29 of 30 emails end with a valid result (no dead jobs, no more
   than one).
Not criteria, only reported: urgency accuracy, order_id and name accuracy,
hallucination rates, latency, injection results.
If a criterion fails, RESULTS.md says so in plain words.

## Procedure
1. Commit emails.jsonl and this file. (Done before any run.)
2. Run the dev set. Tune the prompt and the confidence threshold on dev only.
3. Fill in "Frozen settings" below and commit. Then run the test set once.
4. Write RESULTS.md from the output, including every wrong case, grouped.

## Frozen settings (fill in after dev tuning, before the test run)
- Model: openai/gpt-oss-120b (Groq)
- Prompt: git commit 6f1034f, prompt_sha256 f7cb253adee40cf0 (prompt v2)
- Confidence threshold: 0.7 (not tuned; nothing on dev fell near it)
- reasoning_effort / temperature: low / 0

## Tuning log (dev set only)
- Prompt v1 on dev: category 9/10, urgency 8/10.
- Prompt v2 adds the category and urgency definitions from the labeling rules,
  written after seeing v1's dev errors (two urgency misses, one category miss).
  Adoption rule, set before the v2 run: adopt only if dev category + urgency
  correct is at least 17/20. v2 scored 19/20, so v2 is adopted.
- One revision only. No further tuning on dev or test.
- Dev scores are now optimistic: the v2 prompt was informed by dev errors.
- The adoption rule was agreed in the author's conversation with the AI assistant before the v2 run; git first records it in commit 050dc54, after the run, so the timing rests on the author's account.

## Reruns
A run that is invalid because of infrastructure (API outage, network, rate
limits that killed jobs) may be repeated, and RESULTS.md must say so. A rerun
because the numbers were disappointing is not allowed.

## Known limitations (to repeat in RESULTS.md)
- 30 test emails is a small sample: one email is 3.3 percentage points.
- Labels were drafted by an LLM and reviewed by one person.
- The emails are synthetic, shorter and cleaner than real support mail.
- Exact-match scoring does not measure summary or reply quality.
- The prompt and threshold were tuned on only 10 dev emails.