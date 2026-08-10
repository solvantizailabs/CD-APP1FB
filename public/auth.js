/**
 * Firebase Authentication Handler
 * Manages session persistence, login/logout, and user state
 */

class AuthManager {
    constructor() {
        this.currentUser = null;
        this.userData = null;
        this.init();
    }

    init() {
        // Listen for auth state changes (session persistence)
        firebase.auth().onAuthStateChanged(async (user) => {
            if (user) {
                console.log('[AUTH] User logged in:', user.email);
                this.currentUser = user;
                await this.loadUserData();
                this.updateUI();
            } else {
                console.log('[AUTH] User logged out');
                this.currentUser = null;
                this.userData = null;
                this.updateUI();
            }
        });
    }

    async loadUserData() {
        try {
            const userDoc = await db.collection('users').doc(this.currentUser.uid).get();
            if (userDoc.exists) {
                this.userData = userDoc.data();
                const classValue = String(this.userData.class || '').replace(/\D/g, '');
                this.userData.class = classValue;
                window.userData = this.userData;
                window.currentUserClass = classValue;

                // Store in localStorage for quick access
                localStorage.setItem('userClass', classValue);
                localStorage.setItem('userRole', this.userData.role || '');
                localStorage.setItem('userName', this.userData.name || '');

                console.log('[AUTH] User data loaded:', this.userData);
            } else {
                console.warn('[AUTH] No user document found in Firestore');
            }
        } catch (error) {
            console.error('[AUTH] Error loading user data:', error);
        }
    }

    updateUI() {
        const navCta = document.querySelector('.lg-nav-cta');
        if (!navCta) return;

        if (this.currentUser && this.userData) {
            // Create profile dropdown directly inside navbar instead of using floating component
            const avatarDisplay = this.userData.avatar || (this.userData.name || 'S').charAt(0).toUpperCase();
            const isEmoji = this.userData.avatar && this.userData.avatar.length <= 2;

            navCta.innerHTML = `
                <div class="navbar-profile-dropdown" style="position: relative;">
                    <button class="navbar-profile-trigger" id="navbar-profile-trigger" onclick="toggleNavbarProfileDropdown(event)">
                        <div class="navbar-profile-avatar" style="${isEmoji ? 'font-size: 1.25rem;' : ''}">${avatarDisplay}</div>
                        <div class="navbar-profile-info">
                            <div class="navbar-profile-name">${this.userData.name || 'Student'}</div>
                            <div class="navbar-profile-class">Class ${this.userData.class || '-'}</div>
                        </div>
                        <svg class="navbar-profile-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M6 9l6 6 6-6" stroke-linecap="round" stroke-linejoin="round"/>
                        </svg>
                    </button>

                    <div class="navbar-profile-menu" id="navbar-profile-menu">
                        <div class="navbar-profile-menu-header">
                            <div class="navbar-profile-menu-avatar" style="${isEmoji ? 'font-size: 2.5rem;' : ''}">${avatarDisplay}</div>
                            <div class="navbar-profile-menu-info">
                                <div class="navbar-profile-menu-name">${this.userData.name || 'Student'}</div>
                                <div class="navbar-profile-menu-class">Class ${this.userData.class || '-'}</div>
                                ${this.userData.email ? `<div class="navbar-profile-menu-email">${this.userData.email}</div>` : ''}
                            </div>
                        </div>
                        <div class="navbar-profile-menu-divider"></div>
                        <div class="navbar-profile-menu-items">
                            <div class="navbar-profile-menu-item" onclick="goToProfile(); closeNavbarProfileDropdown();">
                                <span class="navbar-profile-menu-icon">👤</span>
                                <span>My Profile</span>
                            </div>
                            <div class="navbar-profile-menu-item logout-item" onclick="authManager.logout(); closeNavbarProfileDropdown();">
                                <span class="navbar-profile-menu-icon">🚪</span>
                                <span>Logout</span>
                            </div>
                        </div>
                    </div>
                </div>
            `;
        } else {
            // Show admin login only
            navCta.innerHTML = `
                <a href="/admin-login.html" class="btn small outline">Admin Login</a>
            `;
        }

        // Auto-populate class dropdowns if logged in
        if (this.userData && this.userData.class) {
            document.querySelectorAll('.class-select').forEach(select => {
                select.value = this.userData.class;
                select.disabled = true;
            });
        }
    }

    async login(email, password) {
        try {
            const userCredential = await firebase.auth().signInWithEmailAndPassword(email, password);
            // Explicitly set current user and load their data to avoid race conditions.
            // onAuthStateChanged will also fire but this ensures data is ready immediately.
            this.currentUser = userCredential.user;
            await this.loadUserData();
            return { success: true, user: this.currentUser, userData: this.userData };
        } catch (error) {
            console.error('[AUTH] Login error:', error);
            // Clear user data on login failure
            this.currentUser = null;
            this.userData = null;
            return { success: false, error: error.message };
        }
    }

    async logout() {
        const confirm = window.confirm('Are you sure you want to logout?');
        if (!confirm) return;

        try {
            await firebase.auth().signOut();
            this.currentUser = null;
            this.userData = null;
            localStorage.clear();
            sessionStorage.clear();
            this.updateUI();
            window.location.href = '/';
        } catch (error) {
            console.error('[AUTH] Logout error:', error);
            alert('Error logging out. Please try again.');
        }
    }

    isAuthenticated() {
        return this.currentUser !== null;
    }

    hasRole(role) {
        return this.userData && this.userData.role === role;
    }

    async updateUserClass(classNum) {
        if (!this.currentUser) return { success: false, error: 'Not authenticated' };
        const normalizedClass = String(classNum || '').replace(/\D/g, '');

        try {
            await db.collection('users').doc(this.currentUser.uid).update({
                class: normalizedClass
            });
            this.userData.class = normalizedClass;
            localStorage.setItem('userClass', normalizedClass);
            this.updateUI();
            return { success: true };
        } catch (error) {
            console.error('[AUTH] Error updating class:', error);
            return { success: false, error: error.message };
        }
    }

    async updateUserProfile(data) {
        if (!this.currentUser) return { success: false, error: 'Not authenticated' };

        try {
            const payload = { ...data };
            if (payload.class) {
                payload.class = String(payload.class || '').replace(/\D/g, '');
            }
            await db.collection('users').doc(this.currentUser.uid).update(payload);
            // Update local userData
            Object.assign(this.userData, payload);
            // Update localStorage
            if (payload.class) localStorage.setItem('userClass', payload.class);
            if (payload.avatar) localStorage.setItem('userAvatar', payload.avatar);
            this.updateUI();
            return { success: true };
        } catch (error) {
            console.error('[AUTH] Error updating profile:', error);
            return { success: false, error: error.message };
        }
    }

    // Check if user needs to complete profile (class, avatar, or response
    // style preference missing - personalized_learning.md SS6.1)
    needsProfileSetup() {
        if (!this.userData || this.userData.role !== 'student') return false;
        const hasStyle = this.userData.preferences && this.userData.preferences.response_style;
        return !this.userData.class || !this.userData.avatar || !hasStyle;
    }

    // Legacy method - kept for compatibility
    needsClassSelection() {
        return this.needsProfileSetup();
    }

    /**
     * Enforce authentication on protected pages.
     * If not logged in, saves current URL and redirects to login.
     */
    requireAuth() {
        // Give Firebase a moment to restore session
        const unsubscribe = firebase.auth().onAuthStateChanged(user => {
            unsubscribe(); // Run once
            if (!user) {
                console.log('[AUTH] User not logged in, redirecting to login...');
                // Save current URL to return after login
                sessionStorage.setItem('redirect_after_login', window.location.href);
                // Redirect to landing page with login trigger
                window.location.href = '/?login=true';
            } else {
                console.log('[AUTH] User authenticated:', user.uid);
            }
        });
    }

    /**
     * Handle successful login redirection.
     * Checks for saved redirect URL, otherwise defaults to Dashboard.
     */
    handleLoginSuccess() {
        const redirectUrl = sessionStorage.getItem('redirect_after_login');
        if (redirectUrl) {
            console.log('[AUTH] Restoring saved session URL:', redirectUrl);
            sessionStorage.removeItem('redirect_after_login');
            window.location.href = redirectUrl;
        } else {
            console.log('[AUTH] No saved URL, going to User page');
            window.location.href = '/user';
        }
    }
}

// Global auth manager instance
const authManager = new AuthManager();

// Add CSS for user profile
const userProfileStyles = `
<style>
.user-profile {
    display: flex;
    align-items: center;
    gap: 1rem;
}

.user-info {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    color: white;
    font-size: 0.9rem;
}

.user-icon {
    font-size: 1.2rem;
}

.user-name {
    font-weight: 600;
}

.user-class {
    opacity: 0.8;
    font-size: 0.85rem;
}
</style>
`;

// Inject styles
if (!document.querySelector('#user-profile-styles')) {
    const styleEl = document.createElement('div');
    styleEl.id = 'user-profile-styles';
    styleEl.innerHTML = userProfileStyles;
    document.head.appendChild(styleEl);
}

console.log('[AUTH] Auth manager initialized');

// Global helper functions for profile dropdown
function goToProfile() {
    window.location.href = '/profile';
}

function handleLogout() {
    if (typeof authManager !== 'undefined' && authManager) {
        authManager.logout();
    } else {
        // Fallback
        const confirmLogout = window.confirm('Are you sure you want to logout?');
        if (confirmLogout) {
            firebase.auth().signOut().then(() => {
                localStorage.clear();
                window.location.href = '/';
            });
        }
    }
}

// Navbar-specific profile dropdown toggle functions
let navbarProfileDropdownVisible = false;

function toggleNavbarProfileDropdown(event) {
    event.stopPropagation();
    const menu = document.getElementById('navbar-profile-menu');
    const trigger = document.getElementById('navbar-profile-trigger');

    navbarProfileDropdownVisible = !navbarProfileDropdownVisible;

    if (navbarProfileDropdownVisible) {
        menu.classList.add('visible');
        trigger.classList.add('active');
    } else {
        menu.classList.remove('visible');
        trigger.classList.remove('active');
    }
}

function closeNavbarProfileDropdown() {
    navbarProfileDropdownVisible = false;
    const menu = document.getElementById('navbar-profile-menu');
    const trigger = document.getElementById('navbar-profile-trigger');

    if (menu) menu.classList.remove('visible');
    if (trigger) trigger.classList.remove('active');
}

// Close navbar dropdown when clicking outside
document.addEventListener('click', function (event) {
    const dropdown = document.querySelector('.navbar-profile-dropdown');
    if (dropdown && !dropdown.contains(event.target) && navbarProfileDropdownVisible) {
        closeNavbarProfileDropdown();
    }
});

