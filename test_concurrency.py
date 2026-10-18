import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

BASE_URL = "http://127.0.0.1:8000"
TENANT = "concurrency-test"
TOTAL_REQUESTS = 20


def save_memory(i):
    payload = {
        "tenant_id": TENANT,
        "agent_id": f"agent-{i}",
        "content": f"Concurrency test unique memory number {i}",
        "visibility": "shared",
        "memory_type": "semantic",
    }
    start = time.perf_counter()
    response = requests.post(
        f"{BASE_URL}/memories",
        json=payload,
        timeout=30,
    )
    elapsed = time.perf_counter() - start
    return response.status_code, elapsed, response.text


def main():
    started = time.perf_counter()
    results = []

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(save_memory, i) for i in range(TOTAL_REQUESTS)]

        for future in as_completed(futures):
            try:
                results.append(future.result())
            except Exception as exc:
                results.append((0, 0, str(exc)))

    total_time = time.perf_counter() - started
    successes = sum(1 for status, _, _ in results if status == 200)
    failures = len(results) - successes
    latencies = sorted(elapsed for _, elapsed, _ in results if elapsed > 0)

    print(f"Requests: {TOTAL_REQUESTS}")
    print(f"Successful: {successes}")
    print(f"Failed: {failures}")
    print(f"Total wall time: {total_time:.2f}s")

    if latencies:
        median = latencies[len(latencies) // 2]
        print(f"Median request latency: {median:.3f}s")

    for status, _, body in results:
        if status != 200:
            print("FAILURE:", status, body)

    if failures == 0:
        print("RESULT: PASS - all memory writes succeeded")
    else:
        print("RESULT: CHECK FAILURES")


if __name__ == "__main__":
    main()