import sys

from task_scheduling import Scheduler, Scheduling, TaskSpec


def main():
    scheduler = Scheduler("runs/basic", Scheduling(max_parallel_tasks=2))
    scheduler.submit_many([
        TaskSpec("first", "command", payload={"args": [sys.executable, "-c", "print('first completed')"]}),
        TaskSpec("second", "command", dependencies=("first",),
                 payload={"args": [sys.executable, "-c", "print('second completed')"]}),
    ])
    records = scheduler.run()
    for record in records:
        print(f"{record.spec.id}: {record.status}")
    return 0 if all(record.status == "complete" for record in records) else 1


if __name__ == "__main__":
    raise SystemExit(main())
