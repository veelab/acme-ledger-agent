# Acme Appliances Order Ledger Agent

A FastAPI service for the ledger-agent challenge. It accepts `POST /` with
`{"question":"..."}` and returns `{"answer": ...}`. It is deliberately **not**
an MCP endpoint; the grader expects the root URL.

## Deploy to Render

1. Upload this folder to a GitHub repository.
2. In Render, create a **New Web Service** from that repository.
3. Use build command: `pip install -r requirements.txt`
4. Use start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
5. Set environment variables:
   - `EXAM_EMAIL=24f1002845@ds.study.iitm.ac.in`
   - `EXAM_QUIZ_SIGN` to the quizSign from the assignment
   - `EXAM_QUESTION_ID=q-ledger-agent-server`
6. Wait until Render shows the service as live. Submit the base URL, e.g.
   `https://your-service.onrender.com` (no `/mcp` path).

The app loads `/export` and `/rates` at startup. `/export` is newline-delimited
JSON in the observed challenge API. If that response format changes, it falls
back to the paginated `/orders` endpoint. It de-duplicates order IDs by latest
`updated_at`, uses Asia/Kolkata for date filters, and converts currencies using
the API's `usd_per_unit` rates.

## Test

```bash
curl -sS -X POST 'https://YOUR-SERVICE.onrender.com/' \
  -H 'Content-Type: application/json' \
  -d '{"question":"On average, how many US dollars is a paid Central order worth?"}'
```

Expected response shape:

```json
{"answer":123.45}
```

The numeric answer above is only an example of the response shape, not the
computed answer for the live ledger.

## Notes

This starter includes rule-based handling for common revenue, refund, average,
count, product, customer, date, region, currency and order-ID questions. Since
the grader may phrase questions in many ways, test and extend `answer_question`
for any question categories exposed by your challenge. Keep each response fast;
the service caches the ledger in memory rather than downloading it for every
question.
