"""Who may do what with tasks. Roles decide actions; access groups decide visibility.

- VIEWER: may ask questions, may not create or change tasks.
- MEMBER: may create tasks, and change tasks they created or are assigned to.
- ADMIN: may create tasks and change any task in the organisation.
"""

from app.modules.identity import AccessContext
from app.modules.sources import Role
from app.modules.tasks.models import ActionDenied, Task


class TaskPolicy:
    def ensure_can_create(self, access: AccessContext) -> None:
        if access.role is Role.VIEWER:
            raise ActionDenied("Viewers can ask questions but cannot create tasks.")

    def ensure_can_update(self, access: AccessContext, task: Task) -> None:
        if access.role is Role.VIEWER:
            raise ActionDenied("Viewers cannot change tasks.")
        if access.role is Role.ADMIN:
            return
        if access.member_id not in (task.created_by, task.assigned_to):
            raise ActionDenied("Only the creator, the assignee or an admin can change a task.")
