/**
 * Fresh Meat Rotation Planner - Operations JavaScript
 */

// Task completion
async function completeTask(taskId, actor, notes = '') {
    try {
        const result = await apiCall(`/api/tasks/${taskId}/complete/`, 'POST', {
            actor: actor,
            notes: notes,
        });
        
        showToast('Task completed successfully', 'success');
        return result;
    } catch (error) {
        showToast(error.message, 'error');
        throw error;
    }
}

// Package state transitions
async function startFreeze(packageId) {
    try {
        const result = await apiCall('/api/tasks/freeze/start/', 'POST', {
            package_id: packageId,
        });
        
        showToast('Freeze started', 'success');
        return result;
    } catch (error) {
        showToast(error.message, 'error');
        throw error;
    }
}

async function completeFreeze(packageId) {
    try {
        const result = await apiCall('/api/tasks/freeze/complete/', 'POST', {
            package_id: packageId,
        });
        
        showToast('Freeze completed', 'success');
        return result;
    } catch (error) {
        showToast(error.message, 'error');
        throw error;
    }
}

async function startThaw(packageId) {
    try {
        const result = await apiCall('/api/tasks/thaw/start/', 'POST', {
            package_id: packageId,
        });
        
        showToast('Thaw started', 'success');
        return result;
    } catch (error) {
        showToast(error.message, 'error');
        throw error;
    }
}

async function completeThaw(packageId) {
    try {
        const result = await apiCall('/api/tasks/thaw/complete/', 'POST', {
            package_id: packageId,
        });
        
        showToast('Thaw completed, ready for sale', 'success');
        return result;
    } catch (error) {
        showToast(error.message, 'error');
        throw error;
    }
}

async function moveToDisplay(packageId) {
    try {
        const result = await apiCall('/api/tasks/display/start/', 'POST', {
            package_id: packageId,
        });
        
        showToast('Moved to display', 'success');
        return result;
    } catch (error) {
        showToast(error.message, 'error');
        throw error;
    }
}

async function refreeze(packageId) {
    try {
        const result = await apiCall('/api/tasks/display/refreeze/', 'POST', {
            package_id: packageId,
        });
        
        showToast('Refreeze pending', 'success');
        return result;
    } catch (error) {
        showToast(error.message, 'error');
        throw error;
    }
}

// Get today's tasks
async function getTodaysTasks() {
    try {
        const result = await apiCall('/api/tasks/today/');
        return result.tasks;
    } catch (error) {
        showToast('Failed to fetch tasks', 'error');
        return [];
    }
}

// Refresh task display
async function refreshTasks() {
    const tasks = await getTodaysTasks();
    // Update task display
    return tasks;
}
