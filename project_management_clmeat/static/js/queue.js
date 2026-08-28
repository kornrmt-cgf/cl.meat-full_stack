/**
 * Fresh Meat Rotation Planner - Queue JavaScript
 */

// Queue status updates
async function updateQueueStatus(entryId, status) {
    try {
        const result = await apiCall(`/api/plans/queue/${entryId}/status/`, 'POST', {
            status: status,
        });
        
        showToast('Queue status updated', 'success');
        return result;
    } catch (error) {
        showToast(error.message, 'error');
        throw error;
    }
}

// Get queue position
function getQueuePosition(entryId) {
    const row = document.querySelector(`[data-entry-id="${entryId}"]`);
    if (row) {
        return row.querySelector('.queue-position')?.textContent;
    }
    return null;
}

// Refresh queue display
async function refreshQueue() {
    try {
        const result = await apiCall('/api/plans/queue/');
        // Update queue display
        return result.queue;
    } catch (error) {
        showToast('Failed to refresh queue', 'error');
        return [];
    }
}
