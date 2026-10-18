
import uuid
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE_URL = "http://127.0.0.1:8000"
TENANT = f"coord-test-{uuid.uuid4().hex[:8]}"
AGENT = "agent-B"
TOTAL_REQUESTS = 10


def main():
    print("Creating one task...")
    print("Test tenant:", TENANT)

    # 1. Create exactly one task
    response = requests.post(
        f"{BASE_URL}/run-demo",
        json={
            "tenant_id": TENANT,
            "task": "Coordination concurrency test",
            "from_agent": "agent-A",
            "to_agent": AGENT,
        },
        timeout=30,
    )

    print("Create task status:", response.status_code)
    print("Create task response:", response.text)
    response.raise_for_status()

    # 2. Send 10 requests at almost the same time
    print(f"\nSending {TOTAL_REQUESTS} concurrent execute requests...")

    def execute_task():
        try:
            r = requests.post(
                f"{BASE_URL}/execute-task",
                json={
                    "tenant_id": TENANT,
                    "agent_id": AGENT,
                },
                timeout=180,
            )
            try:
                body = r.json()
            except ValueError:
                body = {"raw_response": r.text}
            return r.status_code, body
        except Exception as exc:
            return 0, {"error": str(exc)}

    results = []

    with ThreadPoolExecutor(max_workers=TOTAL_REQUESTS) as pool:
        futures = [
            pool.submit(execute_task)
            for _ in range(TOTAL_REQUESTS)
        ]
        for future in as_completed(futures):
            results.append(future.result())

    # 3. Report results
    success_results = [
        (status, body)
        for status, body in results
        if status == 200
    ]
    not_found = sum(1 for status, _ in results if status == 404)
    other_failures = [
        (status, body)
        for status, body in results
        if status not in (200, 404)
    ]

    print("\n========== RESULTS ==========")
    print("Total requests:", TOTAL_REQUESTS)
    print("Successful (200):", len(success_results))
    print("No task available (404):", not_found)
    print("Other responses/errors:", len(other_failures))

    for i, (status, body) in enumerate(success_results, 1):
        print(f"\nSUCCESS #{i}")
        print("Status:", status)
        print("Response:", body)

    for status, body in other_failures:
        print("\nCHECK THIS RESPONSE")
        print("Status:", status)
        print("Response:", body)

    # A single task should be successfully executed only once.
    if len(success_results) == 1 and not other_failures:
        print("\nRESULT: PASS - exactly one request succeeded.")
    elif len(success_results) > 1:
        print("\nRESULT: WARNING - multiple requests succeeded.")
        print("Inspect task IDs/results to check for duplicate execution.")
    else:
        print("\nRESULT: INVESTIGATE - outcome was not the expected pattern.")


if __name__ == "__main__":
    main()
