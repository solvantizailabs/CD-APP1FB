(function () {
    const SUBJECT_STYLES = {
        science: { gradient: 'linear-gradient(135deg,#10b981,#06b6d4)', icon: '🔬' },
        maths: { gradient: 'linear-gradient(135deg,#f59e0b,#f97316)', icon: '🔢' },
        social: { gradient: 'linear-gradient(135deg,#8b5cf6,#ec4899)', icon: '🌍' },
        english: { gradient: 'linear-gradient(135deg,#6366f1,#4f46e5)', icon: '📖' },
        uncategorized: { gradient: 'linear-gradient(135deg,#64748b,#475569)', icon: '❓' },
    };

    let historyData = null;
    let activeTab = 'videos';

    function styleFor(subject) {
        return SUBJECT_STYLES[subject] || SUBJECT_STYLES.uncategorized;
    }

    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str || '';
        return div.innerHTML;
    }

    function formatDate(isoStr) {
        if (!isoStr) return '';
        const d = new Date(isoStr);
        if (isNaN(d.getTime())) return '';
        return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    }

    function getUid() {
        return (window.currentUser && window.currentUser.uid)
            || (typeof firebase !== 'undefined' && firebase.auth && firebase.auth().currentUser ? firebase.auth().currentUser.uid : null);
    }

    function getClassName() {
        return String(window.currentUserClass || localStorage.getItem('userClass') || '').replace(/\D/g, '');
    }

    window.openHistoryModal = function () {
        const modal = document.getElementById('history-modal');
        if (!modal) return;
        modal.classList.add('open');

        const avatarText = (document.getElementById('profile-avatar-letter') || {}).textContent;
        const nameText = (document.getElementById('profile-user-name') || {}).textContent;
        const gradeText = (document.getElementById('profile-user-grade') || {}).textContent;
        const avatarEl = document.getElementById('history-profile-avatar');
        const nameEl = document.getElementById('history-profile-name');
        const gradeEl = document.getElementById('history-profile-grade');
        if (avatarEl && avatarText) avatarEl.textContent = avatarText;
        if (nameEl && nameText) nameEl.textContent = nameText;
        if (gradeEl && gradeText) gradeEl.textContent = gradeText;

        fetchHistory();
    };

    window.closeHistoryModal = function () {
        const modal = document.getElementById('history-modal');
        if (modal) modal.classList.remove('open');
    };

    window.switchHistoryTab = function (tab) {
        activeTab = tab;
        document.getElementById('history-tab-videos').classList.toggle('active', tab === 'videos');
        document.getElementById('history-tab-questions').classList.toggle('active', tab === 'questions');
        document.getElementById('history-panel-videos').style.display = tab === 'videos' ? 'block' : 'none';
        document.getElementById('history-panel-questions').style.display = tab === 'questions' ? 'block' : 'none';
        if (historyData) updateEmptyState();
    };

    async function fetchHistory() {
        const uid = getUid();
        const loadingEl = document.getElementById('history-loading');
        const emptyEl = document.getElementById('history-empty');
        const videosPanel = document.getElementById('history-panel-videos');
        const questionsPanel = document.getElementById('history-panel-questions');

        if (!uid) {
            loadingEl.textContent = 'Could not identify the current student. Try refreshing the page.';
            return;
        }

        loadingEl.style.display = 'block';
        emptyEl.style.display = 'none';
        videosPanel.innerHTML = '';
        questionsPanel.innerHTML = '';

        try {
            const params = new URLSearchParams({ uid, class_name: getClassName() });
            const resp = await fetch(`/api/history?${params.toString()}`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            historyData = await resp.json();
            loadingEl.style.display = 'none';

            populateSubjectFilter(historyData.subjects_available || []);
            renderHistory(historyData);
        } catch (e) {
            console.error('[history] Failed to fetch history:', e);
            loadingEl.textContent = 'Could not load history. Please try again later.';
        }
    }

    function populateSubjectFilter(subjects) {
        const select = document.getElementById('history-subject-filter');
        select.innerHTML = '<option value="all">All subjects</option>';
        subjects.forEach((s) => {
            const opt = document.createElement('option');
            opt.value = s;
            opt.textContent = s.charAt(0).toUpperCase() + s.slice(1);
            select.appendChild(opt);
        });
        select.onchange = () => renderHistory(historyData);
    }

    function renderHistory(data) {
        const videosPanel = document.getElementById('history-panel-videos');
        const questionsPanel = document.getElementById('history-panel-questions');
        const emptyEl = document.getElementById('history-empty');
        videosPanel.innerHTML = '';
        questionsPanel.innerHTML = '';

        const filterVal = document.getElementById('history-subject-filter').value;
        const groups = (data.groups || []).filter((g) => filterVal === 'all' || g.subject === filterVal);

        groups.forEach((group) => {
            const videoItems = group.items.filter((i) => i.has_video);
            const textItems = group.items;

            if (videoItems.length > 0) {
                videosPanel.appendChild(renderVideoGroup(group.subject, videoItems));
            }
            if (textItems.length > 0) {
                questionsPanel.appendChild(renderQuestionGroup(group.subject, textItems));
            }
        });

        updateEmptyState();
    }

    // Empty-state message reflects the CURRENTLY ACTIVE tab's own panel, not a
    // global item count - a student with only text answers and zero videos
    // should see "No videos yet" while on the videos tab, not have the
    // message hidden just because their questions tab has content.
    function updateEmptyState() {
        const emptyEl = document.getElementById('history-empty');
        const activePanel = document.getElementById(activeTab === 'videos' ? 'history-panel-videos' : 'history-panel-questions');
        const isEmpty = activePanel.children.length === 0;
        emptyEl.style.display = isEmpty ? 'block' : 'none';
        emptyEl.textContent = activeTab === 'videos'
            ? "No videos yet. Ask a question that needs a video lesson to see one here."
            : 'No history yet. Ask a question to get started.';
    }

    function renderVideoGroup(subject, items) {
        const wrap = document.createElement('div');
        wrap.className = 'history-subject-group';

        const label = document.createElement('p');
        label.className = 'history-subject-group-label';
        label.textContent = subject;
        wrap.appendChild(label);

        const grid = document.createElement('div');
        grid.className = 'history-video-grid';

        items.forEach((item) => {
            const style = styleFor(subject);
            const card = document.createElement('div');
            card.className = 'history-video-card';
            card.innerHTML = `
                <div class="history-video-thumb" style="background:${style.gradient};">
                    <span>${style.icon}</span>
                    <span class="history-video-badge">▶ Lesson</span>
                    <button class="history-video-save-btn" title="Save to Visual Library">🎒</button>
                </div>
                <div class="history-video-body">
                    <div class="history-video-avatar" style="background:${style.gradient};">${style.icon}</div>
                    <div>
                        <p class="history-video-title">${escapeHtml(item.chapter_name)}</p>
                        <p class="history-video-sub">${escapeHtml(item.query)}</p>
                    </div>
                </div>`;
            card.onclick = () => replayHistoryItem(item.doc_id);
            const saveBtn = card.querySelector('.history-video-save-btn');
            if (saveBtn) {
                saveBtn.onclick = (e) => {
                    e.stopPropagation();
                    saveVideoToLibrary(item.doc_id, saveBtn);
                };
            }
            grid.appendChild(card);
        });

        wrap.appendChild(grid);
        return wrap;
    }

    function renderQuestionGroup(subject, items) {
        const wrap = document.createElement('div');
        wrap.className = 'history-subject-group';

        const label = document.createElement('p');
        label.className = 'history-subject-group-label';
        label.textContent = subject;
        wrap.appendChild(label);

        const list = document.createElement('div');
        list.className = 'history-question-list';

        items.forEach((item) => {
            const style = styleFor(subject);
            const row = document.createElement('div');
            row.className = 'history-question-row';
            row.innerHTML = `
                <div class="history-question-icon" style="background:${style.gradient};">${item.has_video ? '▶' : '💬'}</div>
                <div class="history-question-text">
                    <p class="history-question-title">${escapeHtml(item.chapter_name)}</p>
                    <p class="history-question-sub">${escapeHtml(item.query)}</p>
                </div>
                <span class="history-question-date">${formatDate(item.timestamp)}</span>
                <span class="history-question-chevron">›</span>`;
            row.onclick = () => replayHistoryItem(item.doc_id);
            list.appendChild(row);
        });

        wrap.appendChild(list);
        return wrap;
    }

    async function saveVideoToLibrary(docId, btnEl) {
        const uid = getUid();
        if (!uid) return;

        try {
            const resp = await fetch('/api/bag/visual-library/add', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid, doc_id: docId }),
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            if (btnEl) {
                btnEl.textContent = '✅';
                setTimeout(() => { btnEl.textContent = '🎒'; }, 1500);
            }
        } catch (e) {
            console.error('[history] Failed to save video to library:', e);
            if (btnEl) {
                btnEl.textContent = '⚠️';
                setTimeout(() => { btnEl.textContent = '🎒'; }, 1500);
            }
        }
    }

    async function replayHistoryItem(docId) {
        const uid = getUid();
        if (!uid) return;

        window.closeHistoryModal();

        try {
            const resp = await fetch('/api/history/replay', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ uid, doc_id: docId }),
            });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const replayData = await resp.json();

            if (!replayData.llm_response && !replayData.video_url) {
                replayData.llm_response = "This is an older question from before we started saving full answers here, so there's nothing to show for it - try asking it again to get a fresh answer.";
            }

            if (typeof window.injectReplayedTurn === 'function') {
                window.injectReplayedTurn(replayData);
            } else {
                console.error('[history] injectReplayedTurn is not available yet.');
            }
        } catch (e) {
            console.error('[history] Failed to replay history item:', e);
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        const btn = document.getElementById('history-btn');
        if (btn) btn.onclick = () => window.openHistoryModal();
    });
})();
