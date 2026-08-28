"""
Operations Models: WorkerTask, TaskEvent, RotationEvent
"""
from django.db import models


class TaskType(models.TextChoices):
    """Types of worker tasks."""
    FREEZE_START = 'FREEZE_START'
    FREEZE_CHECK = 'FREEZE_CHECK'
    MOVE_TO_THAW_QUEUE = 'MOVE_TO_THAW_QUEUE'
    THAW_START = 'THAW_START'
    THAW_CHECK = 'THAW_CHECK'
    THAW_COMPLETE = 'THAW_COMPLETE'
    MOVE_TO_DISPLAY = 'MOVE_TO_DISPLAY'
    REFREEZE = 'REFREEZE'
    PROCESS = 'PROCESS'
    DISCARD = 'DISCARD'


class TaskStatus(models.TextChoices):
    """Status choices for WorkerTask."""
    PENDING = 'PENDING'
    IN_PROGRESS = 'IN_PROGRESS'
    COMPLETED = 'COMPLETED'
    SKIPPED = 'SKIPPED'
    OVERDUE = 'OVERDUE'
    CANCELLED = 'CANCELLED'


class WorkerTask(models.Model):
    """Operational task for workers to execute."""
    
    id = models.AutoField(primary_key=True)
    package = models.ForeignKey('inventory.Package', on_delete=models.PROTECT, related_name='worker_tasks')
    rotation_plan = models.ForeignKey('planning.RotationPlan', on_delete=models.PROTECT, related_name='worker_tasks')
    task_type = models.CharField(max_length=30, choices=TaskType.choices)
    scheduled_at = models.DateTimeField(help_text="When task should be performed")
    status = models.CharField(
        max_length=20, 
        choices=TaskStatus.choices, 
        default=TaskStatus.PENDING
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.CharField(max_length=100, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['scheduled_at']
        verbose_name = 'Worker Task'
        verbose_name_plural = 'Worker Tasks'
    
    def __str__(self):
        return f"{self.get_task_type_display()}: {self.package.display_name}"
    
    @property
    def is_overdue(self):
        """Check if task is overdue."""
        from django.utils import timezone
        if self.status in [TaskStatus.COMPLETED, TaskStatus.CANCELLED, TaskStatus.SKIPPED]:
            return False
        return timezone.now() > self.scheduled_at


class TaskEvent(models.Model):
    """Event log for worker task actions."""
    
    id = models.AutoField(primary_key=True)
    task = models.ForeignKey(WorkerTask, on_delete=models.CASCADE, related_name='events')
    event_type = models.CharField(max_length=50)
    timestamp = models.DateTimeField()
    actor = models.CharField(max_length=100, blank=True, default='')
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['timestamp']
        verbose_name = 'Task Event'
        verbose_name_plural = 'Task Events'
    
    def __str__(self):
        return f"{self.event_type}: {self.task}"


class RotationEvent(models.Model):
    """Audit trail for package state transitions."""
    
    id = models.AutoField(primary_key=True)
    package = models.ForeignKey('inventory.Package', on_delete=models.CASCADE, related_name='rotation_events')
    event_type = models.CharField(max_length=50)
    from_state = models.CharField(max_length=20)
    to_state = models.CharField(max_length=20)
    timestamp = models.DateTimeField()
    actor = models.CharField(max_length=100, blank=True, default='')
    reason = models.TextField(blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['timestamp']
        verbose_name = 'Rotation Event'
        verbose_name_plural = 'Rotation Events'
    
    def __str__(self):
        return f"{self.event_type}: {self.from_state} → {self.to_state}"
