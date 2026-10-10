
def execute_job(job: dict) -> dict:
    action = job.get("action")
    args = job.get("args", {})

    if action == "ping":
        print("Ping executed locally")
        return {"message": "pong"}

    if action == "add":
        a = args["a"]
        b = args["b"]

        result = a + b

        print(f"Local computation: {a} + {b} = {result}")

        return {"sum": result}

    raise ValueError(f"Unsupported action: {action}")
