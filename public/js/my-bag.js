/**
 * My Bag - Premium Animated Feature
 * Works with existing HTML modal structure
 */

class MyBag {
    constructor() {
        this.isOpen = false;
        this.currentNotebook = null;
        this.currentNotebookId = null;
        this.uid = null;
        this.selectedColor = '#6366f1';
        this.notebooks = []; // Initialize notebooks array

        this.init();
    }

    init() {
        // Get auth state
        if (window.firebase) {
            firebase.auth().onAuthStateChanged(user => {
                if (user) {
                    this.uid = user.uid;
                }
            });
        }

        // Wire up create notebook form if it exists
        this.setupModalHandlers();
    }

    setupModalHandlers() {
        // Add event listener for color selection
        document.addEventListener('click', (e) => {
            if (e.target.classList.contains('color-btn')) {
                document.querySelectorAll('.color-btn').forEach(btn => btn.classList.remove('selected'));
                e.target.classList.add('selected');
                this.selectedColor = e.target.dataset.color;
                const hiddenInput = document.getElementById('notebook-color');
                if (hiddenInput) hiddenInput.value = this.selectedColor;
            }
        });
    }

    open() {
        console.log('[MY BAG] Opening My Bag sidebar...');

        // Show My Bag overlay and sidebar
        const overlay = document.getElementById('my-bag-overlay');
        const sidebar = document.getElementById('my-bag-sidebar');

        if (!overlay) {
            console.error('[MY BAG] ❌ Overlay element not found!');
        } else {
            overlay.classList.add('active');
            console.log('[MY BAG] ✅ Overlay activated');
        }

        if (!sidebar) {
            console.error('[MY BAG] ❌ Sidebar element not found!');
        } else {
            sidebar.classList.add('active');
            console.log('[MY BAG] ✅ Sidebar activated (should slide in from right)');
        }

        // Load notebooks
        this.loadNotebooks();
    }

    close() {
        const overlay = document.getElementById('my-bag-overlay');
        const sidebar = document.getElementById('my-bag-sidebar');

        if (overlay) overlay.classList.remove('active');
        if (sidebar) sidebar.classList.remove('active');
    }

    async loadNotebooks() {
        if (!this.uid) {
            console.error('[MY BAG] No user ID available');
            return;
        }

        try {
            console.log('[MY BAG] Fetching notebooks for uid:', this.uid);
            const response = await fetch(`/api/bag/notebooks?uid=${this.uid}`);

            if (!response.ok) {
                console.error('[MY BAG] ❌ API request failed with status:', response.status);
                throw new Error(`HTTP ${response.status}`);
            }

            const data = await response.json();

            console.log('[MY BAG] API Response type:', typeof data);
            console.log('[MY BAG] API Response:', data);

            // Check if response has notebooks property
            let notebooks;
            if (data && typeof data === 'object') {
                if (Array.isArray(data.notebooks)) {
                    notebooks = data.notebooks;
                } else if (Array.isArray(data)) {
                    // In case API returns array directly
                    notebooks = data;
                } else {
                    console.error('[MY BAG] ❌ Unexpected response format:', data);
                    notebooks = [];
                }
            } else {
                console.error('[MY BAG] ❌ Response is not an object:', data);
                notebooks = [];
            }

            console.log('[MY BAG] Parsed notebooks:', notebooks);
            console.log('[MY BAG] Notebooks is array?', Array.isArray(notebooks));

            this.notebooks = notebooks; // Store notebooks on the instance

            const grid = document.getElementById('notebooks-grid');
            if (!grid) {
                console.error('[MY BAG] ❌ notebooks-grid element not found');
                return;
            }

            grid.innerHTML = '';

            if (!Array.isArray(notebooks) || notebooks.length === 0) {
                grid.innerHTML = `
                    <div style="grid-column: 1/-1; text-align: center; padding: 40px; color: rgba(255,255,255,0.5);">
                        <p>No notebooks yet!</p>
                        <p style="font-size: 0.9rem; margin-top: 10px;">Click "Create New Notebook" to get started</p>
                    </div>
                `;
                return;
            }

            notebooks.forEach(nb => {
                const card = document.createElement('div');
                card.className = 'notebook-card';
                card.style.setProperty('--color', nb.color || '#6366f1');
                card.onclick = () => this.openNotebook(nb);

                card.innerHTML = `
                    <div class="notebook-cover" style="background: linear-gradient(135deg, ${nb.color || '#6366f1'}, #1e1b4b); position: relative;">
                        <span style="font-size: 3rem;">📓</span>
                        <button 
                            onclick="event.stopPropagation(); deleteNotebook('${nb.notebook_id}')"
                            style="position: absolute; top: 8px; right: 8px; background: rgba(0,0,0,0.3); border: none; color: white; border-radius: 50%; width: 30px; height: 30px; cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 16px; transition: background 0.2s;"
                            onmouseover="this.style.background='rgba(0,0,0,0.5)'"
                            onmouseout="this.style.background='rgba(0,0,0,0.3)'"
                            title="Delete notebook">
                            ⋮
                        </button>
                    </div>
                    <div class="notebook-title">${nb.name}</div>
                    <div class="notebook-meta">${nb.item_count || 0} items • ${nb.subject}</div>
                `;
                grid.appendChild(card);
            });

            console.log('[MY BAG] ✅ Rendered', notebooks.length, 'notebooks');

        } catch (error) {
            console.error('[MY BAG] Failed to load notebooks:', error);
            const grid = document.getElementById('notebooks-grid');
            if (grid) {
                grid.innerHTML = `
                    <div style="grid-column: 1/-1; text-align: center; padding: 40px; color: rgba(255,100,100,0.8);">
                        <p>❌ Error loading notebooks</p>
                        <p style="font-size: 0.9rem; margin-top: 10px;">${error.message}</p>
                    </div>
                `;
            }
        }
    }

    async loadVisualLibrary() {
        if (!this.uid) {
            console.error('[MY BAG] No user ID available');
            return;
        }

        const grid = document.getElementById('visual-library-grid');
        if (!grid) {
            console.error('[MY BAG] ❌ visual-library-grid element not found');
            return;
        }

        try {
            console.log('[MY BAG] Fetching visual library for uid:', this.uid);
            const response = await fetch(`/api/bag/visual-library?uid=${this.uid}`);
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const data = await response.json();
            const items = Array.isArray(data.items) ? data.items : [];

            grid.innerHTML = '';

            if (items.length === 0) {
                grid.innerHTML = `
                    <div style="grid-column: 1/-1; text-align: center; padding: 40px; color: rgba(255,255,255,0.5);">
                        <p>No saved videos yet!</p>
                        <p style="font-size: 0.9rem; margin-top: 10px;">Save one from History &rarr; My videos with the 🎒 icon.</p>
                    </div>
                `;
                return;
            }

            const SUBJECT_STYLES = {
                science: { gradient: 'linear-gradient(135deg,#10b981,#06b6d4)', icon: '🔬' },
                maths: { gradient: 'linear-gradient(135deg,#f59e0b,#f97316)', icon: '🔢' },
                social: { gradient: 'linear-gradient(135deg,#8b5cf6,#ec4899)', icon: '🌍' },
                english: { gradient: 'linear-gradient(135deg,#6366f1,#4f46e5)', icon: '📖' },
                uncategorized: { gradient: 'linear-gradient(135deg,#64748b,#475569)', icon: '❓' },
            };

            items.forEach((item) => {
                const style = SUBJECT_STYLES[item.subject] || SUBJECT_STYLES.uncategorized;
                const card = document.createElement('div');
                card.className = 'notebook-card visual-library-card';

                card.innerHTML = `
                    <div class="notebook-cover" style="background: ${style.gradient}; position: relative;">
                        <span style="font-size: 3rem;">${style.icon}</span>
                        <span class="visual-library-badge">▶ Lesson</span>
                        <button
                            class="visual-library-remove-btn"
                            title="Remove from Visual Library">
                            ⋮
                        </button>
                    </div>
                    <div class="notebook-title">${item.chapter_name || item.query || 'Video lesson'}</div>
                    <div class="notebook-meta">${item.subject || 'uncategorized'}</div>
                `;

                card.onclick = () => this.replayVideo(item.doc_id);
                const removeBtn = card.querySelector('.visual-library-remove-btn');
                if (removeBtn) {
                    removeBtn.onclick = (e) => {
                        e.stopPropagation();
                        this.removeFromVisualLibrary(item.item_id);
                    };
                }
                grid.appendChild(card);
            });

            console.log('[MY BAG] ✅ Rendered', items.length, 'visual library items');

        } catch (error) {
            console.error('[MY BAG] Failed to load visual library:', error);
            grid.innerHTML = `
                <div style="grid-column: 1/-1; text-align: center; padding: 40px; color: rgba(255,100,100,0.8);">
                    <p>❌ Error loading Visual Library</p>
                    <p style="font-size: 0.9rem; margin-top: 10px;">${error.message}</p>
                </div>
            `;
        }
    }

    // Reuses the exact same replay chain History's "My videos" tab uses
    // (POST /api/history/replay -> window.injectReplayedTurn) so playback,
    // narration, and the "Show Text Answer" toggle all behave identically -
    // no separate player is built for this entry point.
    async replayVideo(docId) {
        if (!this.uid || !docId) return;

        this.close();

        try {
            const resp = await fetch('/api/history/replay', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid: this.uid, doc_id: docId }),
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const replayData = await resp.json();

            if (typeof window.injectReplayedTurn === 'function') {
                window.injectReplayedTurn(replayData);
            } else {
                console.error('[MY BAG] injectReplayedTurn is not available yet.');
            }
        } catch (error) {
            console.error('[MY BAG] Failed to replay video:', error);
        }
    }

    async removeFromVisualLibrary(itemId) {
        if (!this.uid || !itemId) return;

        try {
            const response = await fetch('/api/bag/visual-library/remove', {
                method: 'DELETE',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid: this.uid, item_id: itemId }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            await this.loadVisualLibrary();
        } catch (error) {
            console.error('[MY BAG] Failed to remove visual library item:', error);
            alert('Failed to remove from Visual Library: ' + error.message);
        }
    }

    showCreateModal() {
        const overlay = document.getElementById('create-notebook-overlay');
        if (overlay) {
            overlay.style.display = 'flex';
            overlay.classList.add('active');

            // Reset form
            const nameInput = document.getElementById('notebook-name');
            if (nameInput) nameInput.value = '';
            this.selectedColor = '#6366f1';
        }
    }

    hideCreateModal() {
        const overlay = document.getElementById('create-notebook-overlay');
        if (overlay) {
            overlay.style.display = 'none';
            overlay.classList.remove('active');
        }
    }

    async createNotebook() {
        const name = document.getElementById('notebook-name')?.value;
        const subject = document.getElementById('notebook-subject')?.value || 'general';
        const color = this.selectedColor || '#6366f1';

        console.log('[MY BAG] Creating notebook:', { name, subject, color });

        if (!name || !name.trim()) {
            alert('Please enter a notebook name');
            return;
        }

        if (!this.uid) {
            alert('Please login first');
            return;
        }

        try {
            const requestBody = {
                uid: this.uid,
                name: name.trim(),
                subject: subject,
                color: color
            };

            console.log('[MY BAG] Sending request:', requestBody);

            const response = await fetch('/api/bag/notebooks', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(requestBody)
            });

            const responseData = await response.json();
            console.log('[MY BAG] Create response:', responseData);

            if (response.ok) {
                console.log('[MY BAG] ✅ Notebook created successfully');
                this.hideCreateModal();
                // Reload notebooks to show the new one
                await this.loadNotebooks();
            } else {
                console.error('[MY BAG] ❌ Failed to create notebook:', responseData);
                alert('Failed to create notebook');
            }
        } catch (error) {
            console.error('[MY BAG] Error creating notebook:', error);
            alert('Error creating notebook');
        }
    }

    async openNotebook(notebook) {
        this.currentNotebook = notebook;
        this.currentNotebookId = notebook.notebook_id;

        console.log('[MY BAG] Opening notebook:', notebook);

        // Navigate to the notebook editor page (with /static prefix)
        window.location.href = `/static/notebook-editor.html?id=${notebook.notebook_id}`;
    }

    // Helper to save from chat
    async saveFromChat(content) {
        if (!this.uid) {
            alert("Please login first!");
            return;
        }

        try {
            // Get notebooks
            const response = await fetch(`/api/bag/notebooks?uid=${this.uid}`);
            const notebooks = await response.json();

            if (notebooks.length === 0) {
                // Create a default notebook first
                await fetch('/api/bag/notebooks', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        uid: this.uid,
                        name: 'General Notes',
                        subject: 'general',
                        color: '#6366f1'
                    })
                });
                // Re-fetch
                return this.saveFromChat(content);
            }

            // Save to the first notebook
            const targetNotebook = notebooks[0];

            if (confirm(`Save this to "${targetNotebook.name}"?`)) {
                await fetch('/api/bag/items', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        uid: this.uid,
                        notebook_id: targetNotebook.notebook_id,
                        content: content,
                        title: content.substring(0, 30) + '...'
                    })
                });
                alert("✅ Saved to bag!");
            }
        } catch (error) {
            console.error('Failed to save to bag:', error);
            alert('Error saving to bag');
        }
    }
}

// Initialize
const myBag = new MyBag();
window.myBag = myBag;

// Expose global functions
// My Bag is unified into the standalone /my-bag page now (Notebooks +
// Visual Library) - the old slide-out drawer here is retired as a live
// entry point (kept in place, just unreachable) so there's only one My Bag
// experience instead of two divergent ones.
window.openBag = () => { window.location.href = '/my-bag'; };
window.closeBag = () => myBag.close();
window.showCreateNotebookModal = () => myBag.showCreateModal();
window.hideCreateNotebookModal = () => myBag.hideCreateModal();
window.createNotebook = () => myBag.createNotebook();
window.selectColor = (color) => {
    myBag.selectedColor = color;
    document.getElementById('notebook-color').value = color;
};

// Delete notebook function
window.deleteNotebook = async function (notebookId) {
    if (!confirm('Are you sure you want to delete this notebook? All pages and content will be lost.')) {
        return;
    }

    console.log('[MY BAG] Deleting notebook:', notebookId);

    if (!myBag.uid) {
        alert('Please login first');
        return;
    }

    try {
        // Call backend API to delete
        const response = await fetch('/api/bag/notebook/delete', {
            method: 'DELETE',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                uid: myBag.uid,
                notebook_id: notebookId
            })
        });

        if (!response.ok) {
            const errorData = await response.json();
            throw new Error(errorData.detail || 'Failed to delete notebook');
        }

        console.log('[MY BAG] ✅ Notebook deleted from backend');

        // Reload notebooks from backend
        await myBag.loadNotebooks();

        // Show success message
        const msg = document.createElement('div');
        msg.textContent = '✓ Notebook deleted';
        msg.style.cssText = 'position: fixed; top: 20px; right: 20px; background: #ef4444; color: white; padding: 16px 24px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.3); z-index: 10000; animation: slideIn 0.3s ease;';
        document.body.appendChild(msg);
        setTimeout(() => msg.remove(), 3000);

    } catch (error) {
        console.error('[MY BAG] Error deleting notebook:', error);
        alert('Failed to delete notebook: ' + error.message);
    }
};

// Show Create Note Modal function
window.showCreateNoteModal = function () {
    console.log('[MY BAG] Opening create note modal');

    const overlay = document.getElementById('create-note-overlay');
    if (overlay) {
        overlay.classList.add('active');
        overlay.style.display = 'flex';

        // Populate notebook dropdown
        const select = document.getElementById('note-notebook');
        if (select && myBag.uid) {
            // Load notebooks for the dropdown
            fetch(`/api/bag/notebooks?uid=${myBag.uid}`)
                .then(r => r.json())
                .then(data => {
                    const notebooks = data.notebooks || [];
                    select.innerHTML = notebooks.map(nb =>
                        `<option value="${nb.notebook_id}">${nb.name}</option>`
                    ).join('');
                });
        }
    } else {
        console.error('[MY BAG] Create note overlay not found');
    }
};

window.hideCreateNoteModal = function () {
    const overlay = document.getElementById('create-note-overlay');
    if (overlay) {
        overlay.classList.remove('active');
        overlay.style.display = 'none';
    }
};

window.saveNote = async function () {
    const notebookId = document.getElementById('note-notebook')?.value;
    const title = document.getElementById('note-title')?.value;
    const content = document.getElementById('note-content')?.value;

    if (!notebookId || !title || !content) {
        alert('Please fill in all fields');
        return;
    }

    console.log('[MY BAG] Saving note to notebook:', notebookId);

    // This would save to the notebook's first page
    // For now, redirect to the notebook editor
    window.location.href = `/static/notebook-editor.html?id=${notebookId}`;
};

window.showBagTab = (tabName) => {
    console.log('[MY BAG] Switching to tab:', tabName);

    const tabs = {
        notebooks: { tabEl: document.getElementById('notebooks-tab'), viewEl: document.getElementById('notebooks-view') },
        saved: { tabEl: document.getElementById('saved-tab'), viewEl: document.getElementById('saved-view') },
        'visual-library': { tabEl: document.getElementById('visual-library-tab'), viewEl: document.getElementById('visual-library-view') },
    };

    Object.keys(tabs).forEach((key) => {
        const { tabEl, viewEl } = tabs[key];
        const isActive = key === tabName;
        if (tabEl) tabEl.classList.toggle('active', isActive);
        if (viewEl) viewEl.style.display = isActive ? 'block' : 'none';
    });

    if (tabName === 'visual-library') {
        myBag.loadVisualLibrary();
    }
};

window.filterItems = function () {
    console.log('[MY BAG] Filtering items...');
    // TODO: Implement item filtering
};

console.log('[MY BAG] Initialized successfully');
