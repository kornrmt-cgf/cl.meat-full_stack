/**
 * Fresh Meat Rotation Planner - Modal JavaScript
 */

// Freeze modal
function showFreezeModal(packageId) {
    const body = `
        <form id="freeze-form">
            <div class="form-group">
                <label>Package ID</label>
                <input type="text" class="form-control" value="${packageId}" readonly>
            </div>
            <div class="form-group">
                <label>Freeze Profile</label>
                <select class="form-select" id="freeze-profile" required>
                    <option value="">Select profile...</option>
                </select>
            </div>
            <div class="form-group">
                <label>Start Date</label>
                <input type="date" class="form-control" id="freeze-start-date" required>
            </div>
            <div class="form-group">
                <label>Start Time</label>
                <input type="time" class="form-control" id="freeze-start-time" required>
            </div>
        </form>
    `;
    
    const footer = `
        <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" onclick="submitFreezeForm(${packageId})">Schedule Freeze</button>
    `;
    
    showModal('Schedule Freeze', body, footer);
    
    // Load freeze profiles
    loadFreezeProfiles();
}

async function loadFreezeProfiles() {
    // This would load profiles from API
    const select = document.getElementById('freeze-profile');
    // For now, add placeholder options
    select.innerHTML = `
        <option value="1">Standard Freeze (-18°C)</option>
        <option value="2">Quick Freeze (-25°C)</option>
    `;
}

async function submitFreezeForm(packageId) {
    const profileId = document.getElementById('freeze-profile').value;
    const startDate = document.getElementById('freeze-start-date').value;
    const startTime = document.getElementById('freeze-start-time').value;
    
    if (!profileId || !startDate || !startTime) {
        showToast('Please fill in all fields', 'error');
        return;
    }
    
    try {
        await startFreeze(packageId);
        closeModal();
        location.reload();
    } catch (error) {
        // Error already shown by startFreeze
    }
}

// Thaw queue modal
function showThawQueueModal(packageId) {
    const body = `
        <form id="thaw-queue-form">
            <div class="form-group">
                <label>Package ID</label>
                <input type="text" class="form-control" value="${packageId}" readonly>
            </div>
            <div class="form-group">
                <label>Target Ready Date</label>
                <input type="date" class="form-control" id="target-ready-date" required>
            </div>
            <div class="form-group">
                <label>Target Ready Time</label>
                <input type="time" class="form-control" id="target-ready-time" required>
            </div>
        </form>
    `;
    
    const footer = `
        <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" onclick="submitThawQueueForm(${packageId})">Add to Queue</button>
    `;
    
    showModal('Add to Thaw Queue', body, footer);
}

async function submitThawQueueForm(packageId) {
    const targetDate = document.getElementById('target-ready-date').value;
    const targetTime = document.getElementById('target-ready-time').value;
    
    if (!targetDate || !targetTime) {
        showToast('Please select target ready date and time', 'error');
        return;
    }
    
    const targetReadyAt = `${targetDate}T${targetTime}:00`;
    
    try {
        // First create rotation plan
        await createPlan(packageId, targetReadyAt, 1, 1); // Default profiles
        // Then add to queue
        await addToQueue(packageId);
        closeModal();
        location.reload();
    } catch (error) {
        // Error already shown
    }
}

// Confirm action modal
function showConfirmModal(message, onConfirm) {
    const body = `<p>${message}</p>`;
    const footer = `
        <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button class="btn btn-primary" id="confirm-btn">Confirm</button>
    `;
    
    showModal('Confirm Action', body, footer);
    
    document.getElementById('confirm-btn').addEventListener('click', function() {
        onConfirm();
        closeModal();
    });
}
