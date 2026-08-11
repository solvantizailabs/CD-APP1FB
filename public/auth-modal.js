/**
 * Authentication Modal Functions
 * Handles student login, class selection, and CTA auth checks
 */

let selectedClassNum = null;
let selectedResponseStyle = null;
let pendingRedirect = null;

// Show student login modal
function showStudentLoginModal(redirectTo = null) {
    pendingRedirect = redirectTo;
    const modal = document.getElementById('student-login-modal');
    const modalContent = modal.querySelector('.auth-modal-content');
    const hero = document.querySelector('.landing-main');

    hero.classList.add('hero-fade-out');

    modal.style.display = 'flex';
    setTimeout(() => {
        modalContent.classList.add('modal-slide-in');
    }, 10);
}

// Close student login modal
function closeStudentLoginModal() {
    document.getElementById('student-login-modal').style.display = 'none';
    document.getElementById('login-error').style.display = 'none';
    document.getElementById('student-login-form').reset();
}

// Handle student login form submission
async function handleStudentLogin(event) {
    event.preventDefault();

    const email = document.getElementById('student-email').value;
    const password = document.getElementById('student-password').value;
    const errorDiv = document.getElementById('login-error');

    // Hide previous errors
    errorDiv.style.display = 'none';

    // Attempt login
    const result = await authManager.login(email, password);

    if (result.success) {
        console.log('[AUTH] Login successful');
        closeStudentLoginModal();

        // No more timeout needed, data is loaded.
        if (authManager.needsProfileSetup()) {
            console.log('[AUTH] User needs profile setup');
            showClassSelectionModal();
        } else {
            console.log('[AUTH] Profile complete, redirecting...');
            // Use smart redirect
            authManager.handleLoginSuccess();
        }
    } else {
        errorDiv.textContent = result.error;
        errorDiv.style.display = 'block';
    }
}

// Show class selection modal
function showClassSelectionModal() {
    document.getElementById('class-selection-modal').style.display = 'flex';

    const userData = authManager.userData;
    const hasClass = userData && userData.class;

    // Pre-fill name if it exists
    const nameInput = document.getElementById('student-display-name');
    if (nameInput && userData && userData.name) {
        nameInput.value = userData.name;
    }

    // Populate avatars if not already done
    const avatarGrid = document.getElementById('avatar-grid');
    if (avatarGrid && avatarGrid.children.length === 0) {
        avatarGrid.innerHTML = STUDENT_AVATARS.map(avatar => `
            <div class="avatar-option" data-avatar-id="${avatar.id}" style="
                text-align: center;
                padding: 1rem 0.5rem;
                border: 3px solid #e5e7eb;
                border-radius: 12px;
                cursor: pointer;
                transition: all 0.2s;
            " onclick="selectAvatarInModal('${avatar.id}')">
                <div style="font-size: 3rem;">${avatar.emoji}</div>
                <div style="font-size: 0.7rem; color: #6b7280; margin-top: 0.25rem;">${avatar.name}</div>
            </div>
        `).join('');
    }

    // If user already has a class, pre-select it and hide class step header
    if (hasClass) {
        console.log('[AUTH] User already has class:', userData.class);
        selectedClassNum = userData.class;

        // Hide class selection heading (just show buttons selected)
        const classStep = document.getElementById('class-step');
        if (classStep) {
            const heading = classStep.querySelector('h3');
            if (heading) heading.style.display = 'none';
        }

        // Pre-select the class button
        setTimeout(() => {
            const buttons = document.querySelectorAll('.class-btn');
            buttons.forEach(btn => {
                if (btn.textContent === String(userData.class)) {
                    btn.classList.add('selected');
                }
            });
        }, 100);
    }

    // Pre-select response style if already set (personalized_learning.md SS6.1)
    const existingStyle = userData && userData.preferences && userData.preferences.response_style;
    if (existingStyle) {
        selectedResponseStyle = existingStyle;
        setTimeout(() => {
            const styleBtn = document.querySelector(`.style-option[data-style="${existingStyle}"]`);
            if (styleBtn) styleBtn.classList.add('selected');
            updateSaveButton();
        }, 100);
    }
}

// Close class selection modal
function closeClassSelectionModal() {
    document.getElementById('class-selection-modal').style.display = 'none';
    selectedClassNum = null;
    selectedAvatarId = null;

    // Deselect all buttons
    document.querySelectorAll('.class-btn').forEach(btn => {
        btn.classList.remove('selected');
    });

    // Deselect all avatars
    document.querySelectorAll('.avatar-option').forEach(opt => {
        opt.style.borderColor = '#e5e7eb';
        opt.style.background = 'white';
    });
}

// Select avatar in modal
function selectAvatarInModal(avatarId) {
    // Remove previous selection
    document.querySelectorAll('.avatar-option').forEach(opt => {
        opt.style.borderColor = '#e5e7eb';
        opt.style.background = 'white';
    });

    // Highlight selected
    const selected = document.querySelector(`[data-avatar-id="${avatarId}"]`);
    if (selected) {
        selected.style.borderColor = '#6b5cff';
        selected.style.background = 'rgba(107, 92, 255, 0.05)';
    }

    selectedAvatarId = avatarId;

    // Hide avatar error
    const avatarError = document.getElementById('avatar-error');
    if (avatarError) avatarError.style.display = 'none';

    // Enable button if class is also selected
    updateSaveButton();
}

// Select a class
function selectClass(classNum) {
    selectedClassNum = classNum;

    // Update button states
    document.querySelectorAll('.class-btn').forEach(btn => {
        btn.classList.remove('selected');
    });
    event.target.classList.add('selected');

    // Hide class error
    const classError = document.getElementById('class-error');
    if (classError) classError.style.display = 'none';

    // Enable button if avatar is also selected
    updateSaveButton();
}

// Select a response-style preference (personalized_learning.md SS6.1)
function selectResponseStyle(style) {
    selectedResponseStyle = style;

    document.querySelectorAll('#style-buttons .style-option').forEach(btn => {
        btn.classList.remove('selected');
    });
    const selected = document.querySelector(`.style-option[data-style="${style}"]`);
    if (selected) selected.classList.add('selected');

    const styleError = document.getElementById('style-error');
    if (styleError) styleError.style.display = 'none';

    updateSaveButton();
}

function updateSaveButton() {
    const saveBtn = document.getElementById('save-class-btn');
    const userData = authManager.userData;
    const hasExistingClass = userData && userData.class;
    const hasExistingStyle = userData && userData.preferences && userData.preferences.response_style;

    if (saveBtn) {
        if (selectedAvatarId && (selectedClassNum || hasExistingClass) && (selectedResponseStyle || hasExistingStyle)) {
            saveBtn.disabled = false;
        } else {
            saveBtn.disabled = true;
        }
    }
}

// Save class AND avatar selection
async function saveClassAndAvatar() {
    const nameInput = document.getElementById('student-display-name');
    const nameError = document.getElementById('name-error');
    const avatarError = document.getElementById('avatar-error');
    const classError = document.getElementById('class-error');

    // Validate name
    const displayName = nameInput ? nameInput.value.trim() : '';
    if (!displayName) {
        if (nameError) nameError.style.display = 'block';
        return; // Stop if name is missing
    } else {
        if (nameError) nameError.style.display = 'none';
    }

    // Validate avatar selection
    if (!selectedAvatarId) {
        if (avatarError) {
            avatarError.style.display = 'block';
            avatarError.textContent = 'Please select an avatar';
        }
        return;
    }

    // Check if user already has a class
    const userData = authManager.userData;
    const hasExistingClass = userData && userData.class;

    // If no class selected and user doesn't have one, show error
    if (!selectedClassNum && !hasExistingClass) {
        if (classError) {
            classError.style.display = 'block';
            classError.textContent = 'Please select your class';
        }
        return;
    }

    // Validate response-style preference (personalized_learning.md SS6.1)
    const hasExistingStyle = userData && userData.preferences && userData.preferences.response_style;
    if (!selectedResponseStyle && !hasExistingStyle) {
        const styleError = document.getElementById('style-error');
        if (styleError) styleError.style.display = 'block';
        return;
    }
    const finalResponseStyle = selectedResponseStyle || (userData.preferences && userData.preferences.response_style);

    // Use existing class or newly selected class
    const finalClass = selectedClassNum || userData.class;

    // Find avatar details
    const selectedAvatar = STUDENT_AVATARS.find(a => a.id === selectedAvatarId);

    console.log('[AUTH] Saving profile - Name:', displayName, 'Class:', finalClass, 'Avatar:', selectedAvatar.name, 'Style:', finalResponseStyle);

    // Update user profile in Firestore. `preferences` matches the shape
    // backend/app/services/personalization/profile_service.py reads via
    // get_profile_context() - no separate backend endpoint needed, this is
    // the same direct-client-write pattern class/avatar already use.
    const result = await authManager.updateUserProfile({
        name: displayName,
        class: finalClass,
        avatar: selectedAvatar.emoji,
        avatarId: selectedAvatar.id,
        avatarName: selectedAvatar.name,
        preferences: {
            ...(userData.preferences || {}),
            response_style: finalResponseStyle
        }
    });

    if (result.success) {
        console.log('[AUTH] ✅ Profile updated successfully!');
        closeClassSelectionModal();

        // Go to user page
        window.location.href = '/user';
    } else {
        alert('Error saving profile: ' + result.error);
    }
}

// Check auth before accessing features
function checkAuthAndProceed() {
    if (authManager.isAuthenticated()) {
        // Already logged in - go to user page
        window.location.href = '/user';
    } else {
        // Not logged in - show login modal
        showStudentLoginModal();
    }
}

// Initialize auth check listeners on page load
document.addEventListener('DOMContentLoaded', () => {
    // "Start Learning" button (NEW!)
    const startLearningBtn = document.getElementById('start-learning-btn');
    if (startLearningBtn) {
        startLearningBtn.addEventListener('click', (e) => {
            e.preventDefault();
            checkAuthAndProceed();
        });
    }
});

console.log('[AUTH] Modal functions loaded');
