/**
 * Fresh Meat Rotation Planner - Planner JavaScript
 */

// Plan management functions
async function createPlan(packageId, targetReadyAt, freezeProfileId, thawProfileId) {
    try {
        const result = await apiCall('/api/plans/create/', 'POST', {
            package_id: packageId,
            target_ready_at: targetReadyAt,
            freeze_profile_id: freezeProfileId,
            thaw_profile_id: thawProfileId,
        });
        
        showToast('Rotation plan created successfully', 'success');
        return result;
    } catch (error) {
        showToast(error.message, 'error');
        throw error;
    }
}

async function recalculatePlan(planId) {
    try {
        const result = await apiCall(`/api/plans/${planId}/recalculate/`, 'POST');
        showToast('Plan recalculated', 'success');
        return result;
    } catch (error) {
        showToast(error.message, 'error');
        throw error;
    }
}

// Queue management
async function addToQueue(packageId) {
    try {
        const result = await apiCall('/api/plans/queue/add/', 'POST', {
            package_id: packageId,
        });
        
        showToast('Added to thaw queue', 'success');
        return result;
    } catch (error) {
        showToast(error.message, 'error');
        throw error;
    }
}

async function removeFromQueue(entryId) {
    try {
        const result = await apiCall(`/api/plans/queue/${entryId}/remove/`, 'POST');
        showToast('Removed from queue', 'success');
        return result;
    } catch (error) {
        showToast(error.message, 'error');
        throw error;
    }
}

// Calendar navigation
function navigateMonth(year, month, direction) {
    month += direction;
    if (month > 12) {
        month = 1;
        year++;
    } else if (month < 1) {
        month = 12;
        year--;
    }
    
    window.location.href = `?year=${year}&month=${month}`;
}
