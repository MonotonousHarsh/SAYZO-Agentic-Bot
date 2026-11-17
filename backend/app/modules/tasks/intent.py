"""Decide whether a chat message asks for tasks to be created, or is a question.

Deterministic on purpose: the model never decides to take an action. A message is a task
request only when it starts with an action verb aimed at tasks ("create tasks for ...",
"add a task for ...", "assign Rohan a task ..."). Everything else is answered as a question.
"""

import re

_TASK_REQUEST = re.compile(
    r"^\s*(please\s+)?(can you\s+|could you\s+)?"
    r"(create|add|make|open|log|raise|assign|set up)\b"
    r"[^.?!]{0,80}?\b(tasks?|to-?dos?|action items?)\b",
    re.IGNORECASE,
)


def is_task_request(message: str) -> bool:
    return bool(_TASK_REQUEST.search(message))
