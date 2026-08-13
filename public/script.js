function initApp() {
    // Always register the global submit function first (safe, lazy DOM lookups)
    setupChatSubmitGlobal();

    // Check which page we are on and run the appropriate setup function
    if (document.getElementById('admin-form')) {
        setupAdminPage();
    } else if (document.getElementById('chapters-form')) {
        setupChaptersPage();
    } else if (document.getElementById('user-query-form')) {
        setupUserPage();
    }
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
} else {
    initApp();
}

    // Global visibility change handler to stop TTS (cloud + browser)
    // IMPORTANT: Do NOT stop if an SSE stream is actively running.
    // In AI Voice Mode, Chrome briefly fires visibilitychange when the mic
    // grabs focus — without this guard, stopAll() kills the pipeline right
    // before SSE tokens arrive, causing the "..." stall.
    document.addEventListener('visibilitychange', () => {
        if (document.hidden) {
            const streamIsLive = window.ttsPipeline &&
                window.ttsPipeline.isActive &&
                !window.ttsPipeline.streamCompleted;
            if (streamIsLive) {
                console.log('[Global] Tab hidden during active stream — TTS pipeline protected.');
                return; // Never kill the pipeline mid-stream
            }
            console.log('[Global] Tab hidden, stopping TTS.');
            if (window.playbackController) {
                window.playbackController.stopAll();
            } else {
                if (window.ttsManager) {
                    window.ttsManager.stop();
                } else if (window.speechSynthesis) {
                    window.speechSynthesis.cancel();
                }
                document.querySelectorAll('.speak-btn').forEach(btn => btn.textContent = '🔊');
            }
        }
    });

/**
 * Sets up the main admin page (uploading class, subject, and PDF).
 */
function setupAdminPage() {
    const adminForm = document.getElementById('admin-form');
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('pdf-file');
    const filesContainer = document.getElementById('selected-files-container');
    
    let selectedFiles = [];

    // Click drop zone to select files
    dropZone.addEventListener('click', () => fileInput.click());

    // Drag events
    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            dropZone.classList.add('dragover');
        }, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, (e) => {
            e.preventDefault();
            dropZone.classList.remove('dragover');
        }, false);
    });

    // Drop files
    dropZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const files = dt.files;
        handleFiles(files);
    });

    // Select files via input
    fileInput.addEventListener('change', (e) => {
        handleFiles(e.target.files);
    });

    function handleFiles(files) {
        for (let file of files) {
            if (file.type === 'application/pdf' || file.name.endsWith('.pdf')) {
                // Prevent duplicate files
                if (!selectedFiles.some(f => f.name === file.name && f.size === file.size)) {
                    selectedFiles.push(file);
                }
            }
        }
        renderFileChips();
    }

    function renderFileChips() {
        filesContainer.innerHTML = '';
        selectedFiles.forEach((file, index) => {
            const sizeStr = file.size > 1024 * 1024 
                ? (file.size / (1024 * 1024)).toFixed(2) + ' MB'
                : (file.size / 1024).toFixed(1) + ' KB';
                
            const chip = document.createElement('div');
            chip.className = 'file-chip';
            chip.innerHTML = `
                <span class="file-chip-name" title="${file.name}">${file.name}</span>
                <span class="file-chip-size">(${sizeStr})</span>
                <button type="button" class="file-chip-remove" data-index="${index}">&times;</button>
            `;
            
            chip.querySelector('.file-chip-remove').addEventListener('click', (e) => {
                e.stopPropagation();
                selectedFiles.splice(index, 1);
                renderFileChips();
            });
            
            filesContainer.appendChild(chip);
        });
    }

    adminForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        const className = document.getElementById('class').value;
        const subject = document.getElementById('subject').value;

        if (selectedFiles.length === 0) {
            showStatus('Please upload at least one PDF file.', 'error');
            return;
        }

        if (!className || !subject) {
            showStatus('Please select Class and Subject.', 'error');
            return;
        }

        showStatus(`Uploading ${selectedFiles.length} file(s)...`, 'info');

        const uploadFormData = new FormData();
        selectedFiles.forEach(file => {
            uploadFormData.append('files', file);
        });

        try {
            // Upload multiple files
            const response = await fetch('/api/upload-multiple', {
                method: 'POST',
                body: uploadFormData,
            });
            const uploadResult = await response.json();
            if (!response.ok) {
                throw new Error(uploadResult.detail || 'Failed to upload files.');
            }

            // Redirect to the chapters page with all filenames
            const filenamesParam = JSON.stringify(uploadResult.filenames);
            const queryParams = new URLSearchParams({
                filenames: filenamesParam,
                class_name: className,
                subject: subject
            });
            window.location.href = `/chapters?${queryParams.toString()}`;

        } catch (error) {
            showStatus(`Upload failed: ${error.message}`, 'error');
        }
    });
}

function setupChaptersPage() {
    const params = new URLSearchParams(window.location.search);
    const filenamesParam = params.get('filenames');
    const filename = params.get('filename');
    const className = params.get('class_name');
    const subject = params.get('subject');

    const chaptersForm = document.getElementById('chapters-form');
    const chaptersTableBody = document.getElementById('chapters-table-body');
    const chaptersTableHead = document.querySelector('#chapters-table thead');
    const numChaptersInput = document.getElementById('num-chapters');
    const numChaptersGroup = numChaptersInput ? numChaptersInput.closest('.form-group') : null;
    const extractChaptersBtn = document.getElementById('extract-chapters-btn');
    const extractChaptersGroup = extractChaptersBtn ? extractChaptersBtn.closest('.form-group') : null;

    // Determine Mode
    const isMultiPdf = !!filenamesParam;

    if (isMultiPdf) {
        // Multi-PDF Mode Layout adjustments
        const pageContainer = document.querySelector('.chapters-page-container');
        if (pageContainer) {
            pageContainer.classList.add('multi-pdf-layout');
        }
        
        // Hide PDF extraction controls
        if (numChaptersGroup) numChaptersGroup.style.display = 'none';
        if (extractChaptersGroup) extractChaptersGroup.style.display = 'none';
        
        const headerTitle = document.querySelector('.chapters-form-card .card-header h2');
        if (headerTitle) {
            headerTitle.textContent = 'Review & Verify Chapters';
        }

        // Change table headers dynamically
        if (chaptersTableHead) {
            chaptersTableHead.innerHTML = `
                <tr>
                    <th style="width: 80px; text-align: center;">Academic?</th>
                    <th>Filename</th>
                    <th>Chapter Title</th>
                    <th style="width: 100px;">Start Page</th>
                    <th style="width: 100px;">End Page</th>
                    <th style="width: 150px; text-align: center;">Actions</th>
                </tr>
            `;
        }

        // Fetch pre-analysis results
        const filenames = JSON.parse(filenamesParam);
        showStatus('Running smart pre-analysis to extract chapter metadata...', 'info');

        fetch('/api/books/pre-analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                filenames: filenames,
                class_name: className,
                subject: subject
            })
        })
        .then(res => {
            if (!res.ok) throw new Error('Pre-analysis request failed.');
            return res.json();
        })
        .then(data => {
            showStatus('Pre-analysis complete. Please verify chapter details below.', 'success');
            renderPreAnalyzedRows(data.chapters);
        })
        .catch(err => {
            showStatus(`Pre-analysis failed: ${err.message}. You can still add chapters manually.`, 'error');
            // Fallback: render basic rows
            const fallbackChapters = filenames.map((fn, idx) => ({
                filename: fn,
                is_academic: true,
                chapter_name: fn.replace('.pdf', '').replace(/_/g, ' ').toUpperCase(),
                chapter_no: idx + 1,
                chpstpage: 1,
                chpendpage: 10
            }));
            renderPreAnalyzedRows(fallbackChapters);
        });

        function renderPreAnalyzedRows(chapters) {
            chaptersTableBody.innerHTML = '';
            chapters.forEach(chap => {
                const row = document.createElement('tr');
                row.className = 'chapter-entry';
                row.dataset.filename = chap.filename;
                row.innerHTML = `
                    <td style="text-align: center;"><input type="checkbox" class="is-academic" ${chap.is_academic ? 'checked' : ''}></td>
                    <td style="font-size: 0.85rem; color: #64748b; font-weight: 500; word-break: break-all;">${chap.filename}</td>
                    <td><input type="text" class="chapter-name" value="${chap.chapter_name || ''}" placeholder="e.g., Chemical Effects" required></td>
                    <td><input type="number" class="start-page" value="${chap.chpstpage || ''}" placeholder="e.g., 1" min="1" required></td>
                    <td><input type="number" class="end-page" value="${chap.chpendpage || ''}" placeholder="e.g., 10" min="1" required></td>
                    <td style="text-align: center;">
                        <button type="button" class="action-button small btn-up" style="padding: 4px 8px; margin-right: 2px;">▲</button>
                        <button type="button" class="action-button small btn-down" style="padding: 4px 8px; margin-right: 2px;">▼</button>
                        <button type="button" class="secondary-button small remove-chapter-btn" style="padding: 4px 8px; background: #ef4444; color: white; border: none;">&times;</button>
                    </td>
                `;

                // Up / Down sorting event listeners
                row.querySelector('.btn-up').addEventListener('click', () => {
                    const prev = row.previousElementSibling;
                    if (prev) {
                        row.parentNode.insertBefore(row, prev);
                    }
                });

                row.querySelector('.btn-down').addEventListener('click', () => {
                    const next = row.nextElementSibling;
                    if (next) {
                        row.parentNode.insertBefore(next, row);
                    }
                });

                row.querySelector('.remove-chapter-btn').addEventListener('click', () => {
                    row.remove();
                });

                // Toggle dimming of non-academic rows
                const academicCheckbox = row.querySelector('.is-academic');
                const updateRowDimming = () => {
                    if (academicCheckbox.checked) {
                        row.style.opacity = '1';
                        row.querySelectorAll('input[type="text"], input[type="number"]').forEach(input => input.disabled = false);
                    } else {
                        row.style.opacity = '0.5';
                        row.querySelectorAll('input[type="text"], input[type="number"]').forEach(input => input.disabled = true);
                    }
                };
                academicCheckbox.addEventListener('change', updateRowDimming);
                updateRowDimming(); // Initial state

                chaptersTableBody.appendChild(row);
            });
        }

        // Final form submission for Multi-PDF Mode
        chaptersForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            // Clear previous errors
            document.querySelectorAll('#chapters-table-body .input-error').forEach(el => el.classList.remove('input-error'));

            const rows = document.querySelectorAll('#chapters-table-body tr');
            const chapters = [];
            let validationError = false;

            rows.forEach((row, idx) => {
                const isAcademic = row.querySelector('.is-academic').checked;
                if (!isAcademic) return; // Skip non-academic rows

                const nameInput = row.querySelector('.chapter-name');
                const startPageInput = row.querySelector('.start-page');
                const endPageInput = row.querySelector('.end-page');

                const name = nameInput.value.strip ? nameInput.value.strip() : nameInput.value.trim();
                const start_page = parseInt(startPageInput.value, 10);
                const end_page = parseInt(endPageInput.value, 10);

                let hasRowError = false;
                if (!name) {
                    nameInput.classList.add('input-error');
                    hasRowError = true;
                }
                if (isNaN(start_page) || start_page <= 0) {
                    startPageInput.classList.add('input-error');
                    hasRowError = true;
                }
                if (isNaN(end_page) || end_page < start_page) {
                    endPageInput.classList.add('input-error');
                    hasRowError = true;
                }

                if (hasRowError) {
                    validationError = true;
                } else {
                    chapters.push({
                        chapter_name: name,
                        filename: row.dataset.filename,
                        chpstpage: start_page,
                        chpendpage: end_page,
                        chapter_id: String(idx + 1)
                    });
                }
            });

            if (validationError) {
                showStatus('Please fix the errors in the highlighted fields.', 'error');
                return;
            }

            if (chapters.length === 0) {
                showStatus('Please confirm at least one academic chapter to ingest.', 'error');
                return;
            }

            showStatus('Starting batch ingestion background task...', 'info');

            try {
                const response = await fetch('/api/books/batch-ingest', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        class_name: className,
                        subject: subject,
                        chapters: chapters
                    })
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || 'Failed to trigger batch ingestion.');

                const finalMessage = "Batch Ingestion started successfully. You can now leave this page. All chapters will appear in the dashboard shortly.";
                showStatus(finalMessage, 'success');

                chaptersForm.reset();
                chaptersTableBody.innerHTML = '';
            } catch (error) {
                showStatus(`Ingestion failed: ${error.message}`, 'error');
            }
        });

    } else {
        // Single-PDF Mode (Original Logic)
        if (!filename) {
            document.body.innerHTML = '<h1 style="color: red; text-align: center;">Error: No PDF file specified. Please go back to the admin page and upload a file.</h1>';
            return;
        }

        const pdfUrl = `/uploads/${filename}`;

        // PDF.js state
        let pdfDoc = null;
        let pageNum = 1;
        let pageRendering = false;
        let pageNumPending = null;
        const canvas = document.getElementById('pdf-canvas');
        const ctx = canvas.getContext('2d');

        function renderPage(num) {
            pageRendering = true;
            document.getElementById('pdf-loading-message').style.display = 'block';

            pdfDoc.getPage(num).then(function (page) {
                const container = document.getElementById('pdf-render-area');
                const unscaledViewport = page.getViewport({ scale: 1 });
                const scale = container.clientWidth / unscaledViewport.width;
                const viewport = page.getViewport({ scale: scale });

                canvas.height = viewport.height;
                canvas.width = viewport.width;

                const renderContext = {
                    canvasContext: ctx,
                    viewport: viewport
                };
                const renderTask = page.render(renderContext);

                renderTask.promise.then(function () {
                    pageRendering = false;
                    document.getElementById('pdf-loading-message').style.display = 'none';
                    if (pageNumPending !== null) {
                        renderPage(pageNumPending);
                        pageNumPending = null;
                    }
                });
            });

            document.getElementById('page-num').textContent = num;
        }

        function queueRenderPage(num) {
            if (pageRendering) {
                pageNumPending = num;
            } else {
                renderPage(num);
            }
        }

        pdfjsLib.getDocument(pdfUrl).promise.then(function (pdfDoc_) {
            pdfDoc = pdfDoc_;
            document.getElementById('page-count').textContent = pdfDoc.numPages;
            renderPage(pageNum);
        }).catch(err => {
            showStatus(`Error loading PDF: ${err.message}`, 'error');
            document.getElementById('pdf-loading-message').textContent = 'Error loading PDF.';
        });

        document.getElementById('prev-page').addEventListener('click', () => {
            if (pageNum <= 1) return;
            pageNum--;
            queueRenderPage(pageNum);
        });

        document.getElementById('next-page').addEventListener('click', () => {
            if (pageNum >= pdfDoc.numPages) return;
            pageNum++;
            queueRenderPage(pageNum);
        });

        function createChapterRow() {
            const row = document.createElement('tr');
            row.classList.add('chapter-entry');
            row.innerHTML = `
                <td><input type="text" class="chapter-name" placeholder="e.g., Introduction" required></td>
                <td><input type="number" class="start-page" placeholder="e.g., 1" min="1" required></td>
                <td><input type="number" class="end-page" placeholder="e.g., 10" min="1" required></td>
                <td><button type="button" class="remove-chapter-btn">Remove</button></td>
            `;

            row.querySelector('.remove-chapter-btn').addEventListener('click', () => {
                row.remove();
            });

            return row;
        }

        numChaptersInput.addEventListener('input', () => {
            const count = parseInt(numChaptersInput.value, 10);
            chaptersTableBody.innerHTML = '';

            if (count > 0) {
                for (let i = 0; i < count; i++) {
                    chaptersTableBody.appendChild(createChapterRow());
                }
            }
        });

        chaptersForm.addEventListener('submit', async (e) => {
            e.preventDefault();

            document.querySelectorAll('#chapters-table-body .input-error').forEach(el => el.classList.remove('input-error'));

            const chapterEntries = document.querySelectorAll('#chapters-table-body tr');
            const chapters = [];
            let validationError = false;

            if (chapterEntries.length === 0) {
                showStatus('Please add at least one chapter.', 'error');
                return;
            }

            chapterEntries.forEach(entry => {
                const nameInput = entry.querySelector('.chapter-name');
                const startPageInput = entry.querySelector('.start-page');
                const endPageInput = entry.querySelector('.end-page');

                const name = nameInput.value;
                const start_page = parseInt(startPageInput.value, 10);
                const end_page = parseInt(endPageInput.value, 10);

                let hasRowError = false;
                if (!name) {
                    nameInput.classList.add('input-error');
                    hasRowError = true;
                }
                if (isNaN(start_page) || start_page <= 0) {
                    startPageInput.classList.add('input-error');
                    hasRowError = true;
                }
                if (isNaN(end_page) || end_page < start_page) {
                    endPageInput.classList.add('input-error');
                    hasRowError = true;
                }

                if (hasRowError) {
                    validationError = true;
                } else {
                    chapters.push({
                        chapter_name: name,
                        chpstpage: start_page,
                        chpendpage: end_page
                    });
                }
            });

            if (validationError) {
                showStatus('Please fix the errors in the highlighted fields.', 'error');
                return;
            }

            showStatus('Processing book and chapters...', 'info');

            const finalData = {
                class_name: className,
                subject: subject,
                filename: filename,
                chapters: chapters
            };

            try {
                const response = await fetch('/api/books', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(finalData)
                });
                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || 'Failed to process book.');

                const finalMessage = "Processing started in the background. You can now safely leave this page. The book will be available in a few minutes.";
                showStatus(finalMessage, 'success');

                chaptersForm.reset();
                chaptersTableBody.innerHTML = '';
                numChaptersInput.value = '';
            } catch (error) {
                showStatus(`Error: ${error.message}`, 'error');
            }
        });
    }

    // Add event listeners to clear errors on input
    chaptersTableBody.addEventListener('input', (e) => {
        if (e.target.classList.contains('input-error')) {
            e.target.classList.remove('input-error');
        }
    });
}

/**
 * Lightweight global chat setup — registers window.submitSmartQuery
 * using lazy DOM lookups so it works on the premium user.html page
 * without depending on PDF/viewer elements that no longer exist.
 */
function setupChatSubmitGlobal() {
    // State variables (module-level, shared across calls)
    let _sessionId = null;
    let _turnCount = 0;
    let _isFirstQuery = true;

    window.submitSmartQuery = async function(query, isClickedFollowup = false) {
        // Lazy DOM lookups at call time
        const chatHistory = document.getElementById('chat-history') || document.getElementById('chat-container');
        const submitButton = document.getElementById('submit-query-btn');
        const listChaptersBtn = document.getElementById('list-chapters-btn');

        if (!chatHistory) {
            console.error('[submitSmartQuery] No chat container found (chat-history or chat-container)');
            return;
        }

        if (_isFirstQuery) {
            chatHistory.innerHTML = '';
            _isFirstQuery = false;
        }

        // Show user bubble
        const userRow = document.createElement('div');
        userRow.className = 'chat-bubble-row chat-bubble-row--user';
        const now = new Date();
        const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
        userRow.innerHTML = `<div class="user-bubble-card"><div>${query}</div><div class="user-bubble-meta">${timeStr}</div></div>`;
        chatHistory.appendChild(userRow);
        chatHistory.scrollTop = chatHistory.scrollHeight;

        // Stop any audio/video still playing from a PREVIOUS turn before this
        // new one starts anything of its own. stopAll() below only resets the
        // streaming TTS preview pipeline - it never touches a previously
        // mounted Hyperframes video iframe, which keeps autoplaying its own
        // baked-in scene audio regardless. Confirmed live: asking a second
        // question while the first question's video was still playing caused
        // both audio sources to play simultaneously ("overlapping voices").
        if (window.playbackController) {
            window.playbackController.stopAll();
        }
        document.querySelectorAll('iframe[id^="hf-iframe-"]').forEach(iframe => {
            hfCmd(iframe.id, 'PAUSE');
        });

        if (submitButton) submitButton.setAttribute('disabled', 'true');
        if (listChaptersBtn) listChaptersBtn.classList.add('hidden');
        const currentTurn = _turnCount++;
        // Tracks whether this turn is a video lesson, set from the 'intent'
        // SSE event's format field. Video lessons get their own completion-
        // triggered feedback modal (requestVideoFeedback, fired when the
        // player actually finishes playing) - the inline thumbs row below
        // must not ALSO fire on '[DONE]' for these, since '[DONE]' means the
        // server finished generating, not that the student has finished
        // watching. Firing both is what caused feedback to appear twice.
        let isVideoLesson = false;

        // Show AI loading card — using the existing styled classes from conversation.css
        const aiRow = document.createElement('div');
        aiRow.className = 'chat-bubble-row chat-bubble-row--ai fade-in';
        aiRow.innerHTML = `
            <div class="ai-response-card" id="ai-card-global-${currentTurn}">
                <div class="ai-card-top">
                    <span class="ai-card-title">CHADUVU GURU ASSISTANT</span>
                    <span class="intent-badge-pill" id="intent-badge-${currentTurn}"></span>
                </div>
                <div class="ai-text-content" id="ai-content-${currentTurn}">
                    <span class="thinking-anim"><span></span><span></span><span></span></span>
                </div>
                <div id="video-mount-${currentTurn}"></div>
            </div>`;
        chatHistory.appendChild(aiRow);
        chatHistory.scrollTop = chatHistory.scrollHeight;
        const contentDiv = document.getElementById(`ai-content-${currentTurn}`);

        // Build request
        const studentClass = String(window.currentUserClass || localStorage.getItem('userClass') || '').replace(/\D/g, '');
        const selectedBook = window.selectedBook;

        if (!selectedBook && !studentClass) {
            alert('Your class information is still loading. Please wait a moment or refresh the page.');
            if (submitButton) submitButton.removeAttribute('disabled');
            if (listChaptersBtn) listChaptersBtn.classList.remove('hidden');
            return;
        }

        const params = new URLSearchParams({
            book_uuid: selectedBook ? selectedBook.id : "global",
            query: query,
            class_name: selectedBook ? selectedBook.class_name : studentClass,
            subject: selectedBook ? selectedBook.subject : "all",
            is_clicked_followup: isClickedFollowup.toString()
        });
        if (_sessionId) params.append('session_id', _sessionId);
        // Passed through so the backend can persist a saved-audio copy of
        // text answers using the SAME voice the student is actually hearing
        // live (see analytics_service.log_query's audio synthesis step) -
        // without this, a persisted replay voice could differ from the live
        // one and every save would be a guaranteed cache miss.
        if (window.ttsManager) {
            params.append('tts_model', window.ttsManager.model || 'sarvam');
            params.append('tts_speaker', window.ttsManager.voice || 'ritu');
            params.append('tts_language', window.ttsManager.language || 'en-IN');
        }

        // Attach auth token — use pre-cached token for instant attach (no race timeout)
        try {
            // 1st: Use the globally cached token (set on page load, refreshed every 55min)
            if (window._cachedAuthToken) {
                params.append('token', window._cachedAuthToken);
                console.log('[AUTH] Using cached token for request');
            } else if (typeof firebase !== 'undefined' && firebase.auth) {
                // 2nd: Live fetch with timeout (fallback for first query before cache is set)
                const user = firebase.auth().currentUser;
                if (user) {
                    const tokenPromise = user.getIdToken();
                    const timeoutPromise = new Promise((_, reject) => setTimeout(() => reject('timeout'), 2000));
                    const token = await Promise.race([tokenPromise, timeoutPromise]);
                    if (token) {
                        params.append('token', token);
                        window._cachedAuthToken = token; // update cache
                        console.log('[AUTH] Fetched live token and cached it');
                    }
                }
            }
        } catch(e) {
            console.warn('[AUTH] Token retrieval skipped or timed out:', e);
        }

        // Audio Output & Teacher Reading Chunk Streaming Setup
        const _isAudioOutputMode = true; // Always enable TTS audio pipeline for Teacher Reading Mode
        const useStreamingAudio = _isAudioOutputMode && window.ttsPipeline;
        let fullText = '';

        let bufferedLessonReadyGlobal = null;

        if (useStreamingAudio) {
            window.ttsPipeline.onDisplayChunk = function(textChunk, chunkId) {
                fullText += textChunk;
                if (contentDiv) {
                    contentDiv.innerHTML = (typeof marked !== 'undefined') ? marked.parse(fullText) : fullText;
                    const isNearBottom = (chatHistory.scrollHeight - chatHistory.scrollTop - chatHistory.clientHeight) < 100;
                    if (isNearBottom) chatHistory.scrollTop = chatHistory.scrollHeight;
                }
            };
            window.ttsPipeline.onComplete = function() {
                console.log('[PLAYBACK-GLOBAL] All Teacher Reading chunks complete.');
                if (bufferedLessonReadyGlobal) {
                    console.log('[PLAYBACK-GLOBAL] Mounting video lesson player now.');
                    mountVideoLessonGlobal(currentTurn, bufferedLessonReadyGlobal);
                    bufferedLessonReadyGlobal = null;
                }
                // The answer text finishes visibly displaying/narrating here,
                // not when the SSE stream closes (that only means the backend
                // finished SENDING text - the Teacher Reading pipeline paces
                // on-screen display to match voice narration, which lags
                // behind). Feedback belongs after the student has actually
                // seen/heard the full answer, unless this is a video lesson -
                // those get their own feedback modal timed to actual video
                // playback completion instead (see isVideoLesson above).
                if (!isVideoLesson) {
                    injectFeedbackButtons(currentTurn);
                }
            };
            if (window.playbackController) {
                const cardEl = document.getElementById(`ai-card-global-${currentTurn}`);
                const speakBtn = cardEl ? cardEl.querySelector('.tts-audio-btn') : null;
                window.playbackController.startPipeline(speakBtn);
            } else {
                window.ttsPipeline.start();
            }
            console.log('[STREAM] Teacher Reading Mode (TTS + Chunk Streaming) started.');
        }

        console.log(`[submitSmartQuery] Opening SSE: /api/smart_query?${params.toString()}`);
        const source = new EventSource(`/api/smart_query?${params.toString()}`);

        source.onmessage = function(event) {
            if (event.data === '[DONE]') {
                source.close();
                if (useStreamingAudio) {
                    window.ttsPipeline.flush();
                }
                if (submitButton) submitButton.removeAttribute('disabled');
                if (listChaptersBtn) listChaptersBtn.classList.remove('hidden');
                chatHistory.scrollTop = chatHistory.scrollHeight;
                // Inject feedback thumbs now - but only when there's no
                // Teacher Reading pipeline pacing the on-screen text. When
                // useStreamingAudio is true, the SSE stream closing just
                // means the backend finished SENDING data; the answer may
                // still be visibly displaying/narrating for a while longer,
                // so feedback is deferred to ttsPipeline.onComplete instead
                // (see above) so it appears after the student actually sees
                // the full answer, not while it's still streaming in.
                if (!isVideoLesson && !useStreamingAudio) {
                    injectFeedbackButtons(currentTurn);
                }
                return;
            }
            try {
                const data = JSON.parse(event.data);

                if (data.type === 'intent') {
                    if (data.format === 'VIDEO_REQUIRED') {
                        isVideoLesson = true;
                    }
                    const badge = document.getElementById(`intent-badge-${currentTurn}`);
                    if (badge) {
                        // GENERAL_KNOWLEDGE means this answer wasn't grounded in the
                        // student's ingested textbook content - make that explicit
                        // rather than showing the raw classification label, so
                        // students can tell a syllabus-grounded answer apart from
                        // one answered from general knowledge/web search.
                        badge.textContent = data.intent === 'GENERAL_KNOWLEDGE'
                            ? 'OUTSIDE YOUR TEXTBOOK'
                            : (data.intent || '').replace(/_/g, ' ');
                        badge.className = `intent-badge-pill intent-pill-${(data.intent || '').toLowerCase()}`;
                    }
                    // Apply subject theming
                    const card = document.getElementById(`ai-card-global-${currentTurn}`);
                    const subject = (data.subject || '').toLowerCase();
                    if (card) {
                        if (subject.includes('math')) card.classList.add('subject-math');
                        else if (subject.includes('science')) card.classList.add('subject-science');
                        else if (subject.includes('social') || subject.includes('history')) card.classList.add('subject-social');
                        else if (subject.includes('english')) card.classList.add('subject-english');
                        else card.classList.add('subject-gk');
                    }
                } else if (data.type === 'progress') {
                    // The video pipeline (orchestrator -> storyboard -> voiceover
                    // synthesis) takes 20-40s+ with nothing else shown to the
                    // student in that window - the backend already emits a
                    // message at every step, but it was previously dropped
                    // entirely by this handler, leaving only a generic
                    // "thinking..." animation for the whole wait. Surface it in
                    // place of that animation so the wait feels transparent.
                    if (contentDiv && data.message && !fullText) {
                        contentDiv.innerHTML = `<span class="thinking-anim"><span></span><span></span><span></span></span> <span class="progress-status-text">${data.message}</span>`;
                    }
                } else if (data.type === 'query_id') {
                    // Store the Firestore doc ID on the card for feedback association
                    const card = document.getElementById(`ai-card-global-${currentTurn}`);
                    if (card) card.dataset.queryId = data.query_id;
                    window.lastQueryId = data.query_id;
                } else if (data.display_text) {
                    if (useStreamingAudio) {
                        if (data.audio_url) {
                            window.ttsPipeline.pushPreGeneratedChunk(data.display_text, data.audio_url);
                        } else {
                            window.ttsPipeline.pushToken(data.display_text);
                        }
                    } else {
                        fullText += data.display_text;
                        if (contentDiv) {
                            if (typeof marked !== 'undefined') {
                                contentDiv.innerHTML = marked.parse(fullText);
                            } else {
                                contentDiv.textContent = fullText;
                            }
                            chatHistory.scrollTop = chatHistory.scrollHeight;
                        }
                    }
                } else if (data.type === 'session') {
                    _sessionId = data.session_id || _sessionId;
                } else if (data.type === 'all_scene_audio_ready') {
                    // Backend confirms no more scene_audio_ready chunks are
                    // coming for this lesson - only now can the streaming
                    // pipeline's completion be trusted (see
                    // markStreamComplete's own comment for why the queue
                    // draining alone isn't a safe signal).
                    if (useStreamingAudio && window.ttsPipeline) {
                        window.ttsPipeline.markStreamComplete();
                    }
                } else if (data.type === 'lesson_ready') {
                    // !streamCompleted alone isn't enough - it only means "no
                    // MORE chunks are coming," not "everything already queued
                    // has finished PLAYING." Confirmed live: when the video
                    // compiles quickly right after all_scene_audio_ready
                    // fires, streamCompleted can already be true while the
                    // pipeline is still mid-playback of an earlier scene -
                    // the old check let the video mount immediately anyway,
                    // cutting off the rest of the narration. Must also check
                    // there's no outstanding playback/queue work left.
                    const pipeline = window.ttsPipeline;
                    const stillNarrating = pipeline && pipeline.isActive && (
                        !pipeline.streamCompleted ||
                        pipeline.isProcessingPlayback ||
                        pipeline.deliveryQueue.length > 0 ||
                        pipeline.renderQueue.length > 0
                    );
                    if (stillNarrating) {
                        console.log('[submitSmartQuery Global] Video ready during Teacher Reading! Buffering video mount.');
                        bufferedLessonReadyGlobal = data;
                    } else {
                        mountVideoLessonGlobal(currentTurn, data);
                    }
                } else if (data.error) {
                    if (contentDiv) contentDiv.innerHTML = `<span style="color:#ff6b6b">Error: ${data.error}</span>`;
                    source.close();
                    if (submitButton) submitButton.removeAttribute('disabled');
                }
            } catch(e) {
                console.error('[submitSmartQuery] Parse error:', e, event.data);
            }
        };


        source.onerror = function(e) {
            console.error('[submitSmartQuery] EventSource error:', e);
            source.close();
            if (submitButton) submitButton.removeAttribute('disabled');
            if (contentDiv && !fullText) {
                contentDiv.innerHTML = '<span style="color:#ff6b6b">Connection error. Please try again.</span>';
            }
        };
    };

    // Sibling of submitSmartQuery (not nested inside it) so it is registered
    // once when setupChatSubmitGlobal() runs at page load, and so both a live
    // SSE turn and a replayed history turn (injectReplayedTurn, below) can
    // call it - a version nested inside submitSmartQuery would not exist
    // until submitSmartQuery had been called at least once.
    function mountVideoLessonGlobal(turnId, data) {
            const chatHistory = document.getElementById('chat-history') || document.getElementById('chat-container');
            const mount = document.getElementById(`video-mount-${turnId}`);
            if (!mount || !data.interactive_url) return;

            // The lesson file may no longer exist on disk (e.g. an old video
            // that was never persisted anywhere durable) - mounting an iframe
            // straight to a 404/500 shows the raw server error page inside
            // the player instead of anything useful. Check first so we can
            // show a plain-language fallback and leave the text answer
            // visible instead.
            // redirect: 'manual' instead of the default 'follow' - the backend
            // redirects to a cross-origin Supabase backup when the local file
            // is missing, and a followed cross-origin response can't be read
            // by fetch (CORS blocks it) even though a real iframe navigation
            // to that same URL is unaffected. An unreadable opaqueredirect
            // response means "the backend found it somewhere, trust it," not
            // "it failed."
            fetch(data.interactive_url, { method: 'HEAD', redirect: 'manual' })
                .then((res) => {
                    if (!res.ok && res.type !== 'opaqueredirect') throw new Error(`status ${res.status}`);
                    mountVideoIframe();
                })
                .catch(() => {
                    mount.innerHTML = `<div style="padding: 24px; text-align: center; color: #94a3b8; font-size: 13px;">This video lesson isn't available right now. The text answer above is still there.</div>`;
                });

            function mountVideoIframe() {
                const playerId = `hf-player-${turnId}`;
                const iframeId  = `hf-iframe-${turnId}`;
                const progId    = `hf-prog-${turnId}`;
                const timeId    = `hf-time-${turnId}`;
                const sceneId   = `hf-scene-${turnId}`;

                // By default, hide text answer and card top once video is mounted and ready
                const cardTop = document.querySelector(`#ai-card-global-${turnId} .ai-card-top`);
                const textContent = document.getElementById(`ai-content-${turnId}`);
                if (cardTop) cardTop.style.display = 'none';
                if (textContent) textContent.style.display = 'none';

                // Append the toggle button directly to the card container so it sits in the top-right
                const card = document.getElementById(`ai-card-global-${turnId}`);
                if (card) {
                    const existingBtn = document.getElementById(`hf-toggle-text-${turnId}`);
                    if (existingBtn) existingBtn.remove();
                    const btnContainer = document.createElement('div');
                    btnContainer.className = 'hf-player-top-overlay';
                    btnContainer.style.cssText = 'position: absolute; top: 12px; right: 12px; z-index: 100;';
                    btnContainer.innerHTML = `
                      <button class="hf-overlay-btn" id="hf-toggle-text-${turnId}" onclick="hfToggleTextAnswer('${turnId}')" style="background: rgba(9, 13, 22, 0.75); border: 1px solid rgba(20, 184, 166, 0.45); color: #14b8a6; padding: 6px 12px; border-radius: 8px; font-size: 11px; font-weight: 700; cursor: pointer; backdrop-filter: blur(4px); transition: all 0.2s ease;">
                        Show Text Answer
                      </button>
                    `;
                    card.appendChild(btnContainer);
                }

                mount.innerHTML = `
<div class="hf-player-shell" id="${playerId}" style="position: relative;">
  <!-- Viewport: scales the 1280×720 Hyperframes canvas to fit any width -->
  <div class="hf-viewport-wrapper" id="hf-viewport-${turnId}">
    <div class="hf-scale-box" id="hf-scalebox-${turnId}">
      <iframe id="${iframeId}"
        src="${data.interactive_url}"
        class="hf-iframe"
        allow="autoplay"
        allowfullscreen>
      </iframe>
    </div>
  </div>

  <!-- Controls Bar -->
  <div class="hf-controls">
    <div class="hf-controls-left">
      <button class="hf-btn" id="hf-restart-${turnId}" title="Restart" onclick="hfCmd('${iframeId}','RESTART')">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/></svg>
      </button>
      <button class="hf-btn hf-playpause" id="hf-pp-${turnId}" title="Play/Pause" onclick="hfTogglePlay('${iframeId}','${turnId}')">
        <svg class="icon-play" width="15" height="15" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>
        <svg class="icon-pause" width="15" height="15" viewBox="0 0 24 24" fill="currentColor" style="display:none"><rect x="6" y="3" width="4" height="18"/><rect x="14" y="3" width="4" height="18"/></svg>
      </button>
    </div>

    <div class="hf-controls-center">
      <!-- Progress bar -->
      <span class="hf-time" id="${timeId}">0:00</span>
      <div class="hf-progress-track" id="hf-track-${turnId}"
        onclick="hfSeekClick(event,'${iframeId}','hf-track-${turnId}','hf-ppstate-${turnId}')">
        <div class="hf-progress-fill" id="${progId}" style="width:0%"></div>
        <div class="hf-progress-thumb" id="hf-thumb-${turnId}" style="left:0%"></div>
      </div>
      <span class="hf-time" id="hf-dur-${turnId}">--:--</span>
    </div>

    <div class="hf-controls-right">
      <select class="hf-speed-select" title="Playback Speed"
        onchange="hfCmd('${iframeId}','SET_PLAYBACK_RATE',{rate:parseFloat(this.value)})">
        <option value="0.75">0.75×</option>
        <option value="1" selected>1×</option>
        <option value="1.25">1.25×</option>
        <option value="1.5">1.5×</option>
        <option value="2">2×</option>
      </select>
      <button class="hf-btn hf-mute" id="hf-mute-${turnId}" title="Mute/Unmute"
        onclick="hfToggleMute('${iframeId}','${turnId}')">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 5L6 9H2v6h4l5 4V5z"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/></svg>
      </button>
      <button class="hf-btn" title="Fullscreen" onclick="hfFullscreen('${playerId}')">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
      </button>
      <span class="hf-scene-counter" id="${sceneId}">Scene —</span>
    </div>
  </div>
</div>`;

                // Hidden state element
                const stateEl = document.createElement('span');
                stateEl.id = `hf-ppstate-${turnId}`;
                stateEl.dataset.playing = '0';
                stateEl.dataset.muted = '0';
                stateEl.dataset.duration = '0';
                stateEl.style.display = 'none';
                mount.appendChild(stateEl);

                // Scale the 1280×720 iframe to fill the container responsively
                requestAnimationFrame(() => {
                    hfScaleViewport(`hf-viewport-${turnId}`, `hf-scalebox-${turnId}`);
                });

                // Observe size changes via ResizeObserver for robust layout adaptation
                if (typeof ResizeObserver !== 'undefined') {
                    const wrapperEl = document.getElementById(`hf-viewport-${turnId}`);
                    if (wrapperEl) {
                        const ro = new ResizeObserver(entries => {
                            for (let entry of entries) {
                                if (entry.contentRect.width > 0) {
                                    hfScaleViewport(`hf-viewport-${turnId}`, `hf-scalebox-${turnId}`);
                                }
                            }
                        });
                        ro.observe(wrapperEl);
                        // Save observer reference on the element
                        wrapperEl._resizeObserver = ro;
                    }
                }

                // Re-scale once the iframe is fully loaded in the DOM
                const iframeEl = document.getElementById(iframeId);
                if (iframeEl) {
                    iframeEl.addEventListener('load', () => {
                        hfScaleViewport(`hf-viewport-${turnId}`, `hf-scalebox-${turnId}`);
                    });
                }

                // Listen for postMessage events from this iframe using turnId
                window.addEventListener('message', function hfListener(e) {
                    if (!e.data || e.data.source !== 'HYPERFRAMES_ENGINE') return;
                    const ppBtn   = document.getElementById(`hf-pp-${turnId}`);
                    const prog    = document.getElementById(progId);
                    const thumb   = document.getElementById(`hf-thumb-${turnId}`);
                    const timeEl  = document.getElementById(timeId);
                    const durEl   = document.getElementById(`hf-dur-${turnId}`);
                    const sceneEl = document.getElementById(sceneId);
                    const state   = document.getElementById(`hf-ppstate-${turnId}`);

                    if (e.data.type === 'READY') {
                        const dur = e.data.duration || e.data.totalDuration || 0;
                        if (state) state.dataset.duration = dur;
                        if (durEl && dur > 0) durEl.textContent = hfFmtTime(dur);
                        if (sceneEl && e.data.totalScenes) sceneEl.textContent = `Scene 1 / ${e.data.totalScenes}`;
                    } else if (e.data.type === 'PLAYING') {
                        if (ppBtn) { ppBtn.querySelector('.icon-play').style.display='none'; ppBtn.querySelector('.icon-pause').style.display=''; }
                        if (state) state.dataset.playing = '1';
                    } else if (e.data.type === 'PAUSED') {
                        if (ppBtn) { ppBtn.querySelector('.icon-play').style.display=''; ppBtn.querySelector('.icon-pause').style.display='none'; }
                        if (state) state.dataset.playing = '0';
                    } else if (e.data.type === 'CURRENT_TIME') {
                        const cur = e.data.currentTime || 0;
                        const dur = e.data.duration || 0;
                        if (state) state.dataset.duration = dur;
                        const pct = dur > 0 ? (cur/dur*100).toFixed(1) : 0;
                        if (prog)  prog.style.width = pct + '%';
                        if (thumb) thumb.style.left = pct + '%';
                        if (timeEl) timeEl.textContent = hfFmtTime(cur);
                        if (durEl && dur > 0) durEl.textContent = hfFmtTime(dur);
                    } else if (e.data.type === 'SCENE_CHANGED' || e.data.sceneNo !== undefined) {
                        if (sceneEl) sceneEl.textContent = `Scene ${e.data.sceneNo || e.data.currentScene || ''}`;
                    } else if (e.data.type === 'TIMELINE_FINISHED' || e.data.type === 'LESSON_COMPLETE' || e.data.type === 'PLAYBACK_COMPLETE') {
                        console.log(`[Hyperframes Player] completion event received for turn ${turnId}: ${e.data.type}`);
                        requestVideoFeedback(turnId);
                    }
                });

                chatHistory.scrollTop = chatHistory.scrollHeight;
                console.log('[submitSmartQuery] Hyperframes player mounted:', data.interactive_url);
            }
        }

    // Renders an already-completed history item as if it were just asked,
    // reusing the same turn skeleton and mountVideoLessonGlobal as a live
    // SSE turn, but skipping EventSource entirely since there is nothing
    // to stream - the answer already exists. Shares _turnCount/_sessionId
    // with submitSmartQuery (both siblings inside setupChatSubmitGlobal) so
    // a follow-up right after this replay is sent with the session the
    // backend just set up for it.
    window.injectReplayedTurn = function(data) {
            const chatHistory = document.getElementById('chat-history') || document.getElementById('chat-container');
            if (!chatHistory) {
                console.error('[injectReplayedTurn] No chat container found');
                return;
            }
            if (_isFirstQuery) {
                chatHistory.innerHTML = '';
                _isFirstQuery = false;
            }
            if (data.session_id) _sessionId = data.session_id;

            const userRow = document.createElement('div');
            userRow.className = 'chat-bubble-row chat-bubble-row--user';
            const now = new Date();
            const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
            userRow.innerHTML = `<div class="user-bubble-card"><div>${data.query || ''}</div><div class="user-bubble-meta">${timeStr}</div></div>`;
            chatHistory.appendChild(userRow);

            if (window.playbackController) window.playbackController.stopAll();
            document.querySelectorAll('iframe[id^="hf-iframe-"]').forEach(iframe => hfCmd(iframe.id, 'PAUSE'));

            const currentTurn = _turnCount++;
            const aiRow = document.createElement('div');
            aiRow.className = 'chat-bubble-row chat-bubble-row--ai fade-in';
            aiRow.innerHTML = `
                <div class="ai-response-card" id="ai-card-global-${currentTurn}">
                    <div class="ai-card-top">
                        <span class="ai-card-title">CHADUVU GURU ASSISTANT</span>
                        <span class="intent-badge-pill" id="intent-badge-${currentTurn}"></span>
                    </div>
                    <div class="ai-text-content" id="ai-content-${currentTurn}"></div>
                    <div id="video-mount-${currentTurn}"></div>
                </div>`;
            chatHistory.appendChild(aiRow);

            const contentDiv = document.getElementById(`ai-content-${currentTurn}`);
            if (contentDiv) {
                contentDiv.innerHTML = (typeof marked !== 'undefined') ? marked.parse(data.llm_response || '') : (data.llm_response || '');
            }

            if (data.video_url) {
                mountVideoLessonGlobal(currentTurn, { interactive_url: data.video_url });
            } else if (data.audio_url) {
                // Text-only replay with saved narration audio (see
                // tts_service.synthesize_and_persist_answer_audio) - plays
                // the audio that was actually saved at answer time, not a
                // fresh (billed) TTS call.
                const card = document.getElementById(`ai-card-global-${currentTurn}`);
                if (card) {
                    const playBtn = document.createElement('button');
                    playBtn.className = 'hf-overlay-btn';
                    playBtn.style.cssText = 'margin-top: 8px; background: rgba(20,184,166,0.12); border: 1px solid rgba(20,184,166,0.45); color: #14b8a6; padding: 6px 12px; border-radius: 8px; font-size: 12px; font-weight: 600; cursor: pointer;';
                    playBtn.textContent = '🔊 Play answer';
                    const audioEl = new Audio(data.audio_url);
                    playBtn.onclick = () => {
                        if (audioEl.paused) { audioEl.play(); playBtn.textContent = '⏸ Pause'; }
                        else { audioEl.pause(); playBtn.textContent = '🔊 Play answer'; }
                    };
                    audioEl.onended = () => { playBtn.textContent = '🔊 Play answer'; };
                    card.appendChild(playBtn);
                }
            }

            chatHistory.scrollTop = chatHistory.scrollHeight;
        };

    console.log('[setupChatSubmitGlobal] window.submitSmartQuery registered successfully.');
}

/* ─────────────────────────────────────────────────────────────────────────
   HYPERFRAMES PLAYER GLOBAL HELPERS
   Used by onclick handlers generated inside setupChatSubmitGlobal
   ───────────────────────────────────────────────────────────────────────── */

/** Send a postMessage command to a Hyperframes iframe */
function hfCmd(iframeId, command, extra = {}) {
    const iframe = document.getElementById(iframeId);
    if (!iframe || !iframe.contentWindow) return;
    iframe.contentWindow.postMessage({ target: 'HYPERFRAMES_ENGINE', command, ...extra }, '*');
}

/** Toggle play/pause */
function hfTogglePlay(iframeId, turnId) {
    const state = document.getElementById(`hf-ppstate-${turnId}`);
    const isPlaying = state && state.dataset.playing === '1';
    hfCmd(iframeId, isPlaying ? 'PAUSE' : 'PLAY');
}

/** Toggle mute */
function hfToggleMute(iframeId, turnId) {
    const state = document.getElementById(`hf-ppstate-${turnId}`);
    const isMuted = state && state.dataset.muted === '1';
    const nextMuted = !isMuted;
    if (state) state.dataset.muted = nextMuted ? '1' : '0';
    hfCmd(iframeId, 'TOGGLE_MUTE', { isMuted: nextMuted });
    const btn = document.getElementById(`hf-mute-${turnId}`);
    if (btn) btn.style.opacity = nextMuted ? '0.4' : '1';
}

/** Seek on click on the progress track */
function hfSeekClick(e, iframeId, trackId, stateId) {
    const track = document.getElementById(trackId);
    if (!track) return;
    const state = document.getElementById(stateId);
    const dur = parseFloat((state && state.dataset.duration) || '0');
    if (!dur) return;
    const rect = track.getBoundingClientRect();
    const pct  = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const targetTime = pct * dur;

    // Send the seek command to the Hyperframes iframe
    hfCmd(iframeId, 'SEEK', { targetTime });

    // Update the progress UI immediately for responsiveness
    const prog = document.getElementById(trackId).querySelector('.hf-progress-fill');
    const thumb = document.getElementById(trackId).querySelector('.hf-progress-thumb');
    const timeEl = document.getElementById(trackId.replace('track', 'time'));
    if (prog) prog.style.width = `${(pct * 100).toFixed(1)}%`;
    if (thumb) thumb.style.left = `${(pct * 100).toFixed(1)}%`;
    if (timeEl) timeEl.textContent = hfFmtTime(targetTime);
}

/** Toggle text answer visibility on user request */
function hfToggleTextAnswer(turnId) {
    const cardTop = document.querySelector(`#ai-card-global-${turnId} .ai-card-top`);
    const textContent = document.getElementById(`ai-content-${turnId}`);
    const btn = document.getElementById(`hf-toggle-text-${turnId}`);
    const viewport = document.getElementById(`hf-viewport-${turnId}`);
    const controls = document.querySelector(`#hf-player-${turnId} .hf-controls`);
    const iframeId = `hf-iframe-${turnId}`;

    if (textContent && btn && viewport) {
        const isVideoHidden = viewport.style.display === 'none';
        if (isVideoHidden) {
            // Show video player, hide text answer
            if (cardTop) cardTop.style.display = 'none';
            textContent.style.display = 'none';
            viewport.style.display = 'flex';
            if (controls) controls.style.display = 'flex';
            btn.textContent = 'Show Text Answer';
            
            // Re-scale player viewport
            hfScaleViewport(`hf-viewport-${turnId}`, `hf-scalebox-${turnId}`);
        } else {
            // Show text answer, hide video player, and pause video
            if (cardTop) cardTop.style.display = 'flex';
            textContent.style.display = 'block';
            viewport.style.display = 'none';
            if (controls) controls.style.display = 'none';
            btn.textContent = 'Show Video Lesson';
            
            // Pause the video player
            hfCmd(iframeId, 'PAUSE');
        }
    }
}

/* ─────────────────────────────────────────────────────────────────────────
   STUDENT FEEDBACK SYSTEM
   ───────────────────────────────────────────────────────────────────────── */

const pendingVideoFeedbackTurns = new Set();
const shownVideoFeedbackTurns = new Set();

function requestVideoFeedback(turnId) {
    if (document.fullscreenElement) {
        pendingVideoFeedbackTurns.add(String(turnId));
        console.log(`[Feedback] Deferred until fullscreen exit for turn ${turnId}.`);
        return;
    }
    showVideoFeedbackOverlay(turnId);
}

document.addEventListener('fullscreenchange', () => {
    hfRescaleAllPlayers();
    if (document.fullscreenElement) return;
    pendingVideoFeedbackTurns.forEach((turnId) => {
        pendingVideoFeedbackTurns.delete(turnId);
        showVideoFeedbackOverlay(turnId);
    });
});

/** Display the overlay rating popup inside the video shell when video completes */
function showVideoFeedbackOverlay(turnId, retryCount = 0) {
    const shell = document.getElementById(`hf-player-${turnId}`);
    if (!shell) {
        if (retryCount < 10) {
            console.log(`[Feedback] Player shell not found for turn ${turnId}. Retrying in 100ms... (attempt ${retryCount + 1}/10)`);
            setTimeout(() => showVideoFeedbackOverlay(turnId, retryCount + 1), 100);
        } else {
            console.warn(`[Feedback] Player shell not found for turn ${turnId} after 10 attempts.`);
        }
        return;
    }
    if (shownVideoFeedbackTurns.has(String(turnId))) return;
    shownVideoFeedbackTurns.add(String(turnId));
    
    let overlay = document.getElementById(`hf-feedback-overlay-${turnId}`);
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = `hf-feedback-overlay-${turnId}`;
        overlay.className = 'hf-feedback-overlay';
        overlay.innerHTML = `
            <div class="hf-feedback-dialog" role="dialog" aria-modal="true" aria-label="Lesson feedback">
                <button class="hf-feedback-close" aria-label="Close feedback" title="Close"
                    onclick="skipVideoFeedback('${turnId}')">×</button>
                <div class="hf-feedback-icon">🌟</div>
                <h3 class="hf-feedback-title">Was this lesson helpful?</h3>
                <p class="hf-feedback-subtitle">Your feedback helps us make lessons better.</p>
                <div class="hf-feedback-options">
                    <button class="hf-feedback-btn" onclick="submitVideoFeedback('${turnId}', 'like')">👍 Yes, helpful</button>
                    <button class="hf-feedback-btn" onclick="submitVideoFeedback('${turnId}', 'dislike')">👎 Not quite</button>
                </div>
                <button class="hf-feedback-skip" onclick="skipVideoFeedback('${turnId}')">Skip for now</button>
                <button class="hf-feedback-btn-replay" onclick="replayVideoFromOverlay('${turnId}')">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
                        <path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 .49-3.5"/>
                    </svg>
                    Replay Lesson
                </button>
            </div>
        `;
        // Mount at document level so the iframe/player stacking context cannot
        // intercept clicks on the feedback controls.
        document.body.appendChild(overlay);
    }
    
    // Show overlay smoothly
    requestAnimationFrame(() => overlay.classList.add('fb-show'));
}

window.skipVideoFeedback = function(turnId) {
    const overlay = document.getElementById(`hf-feedback-overlay-${turnId}`);
    if (overlay) overlay.classList.remove('fb-show');
};

window.submitVideoFeedback = function(turnId, type) {
    // Hide overlay
    const overlay = document.getElementById(`hf-feedback-overlay-${turnId}`);
    if (overlay) {
        overlay.classList.remove('fb-show');
    }
    
    // Call standard feedback submission pipeline
    submitFeedback(turnId, type);
};

window.replayVideoFromOverlay = function(turnId) {
    const iframeId = `hf-iframe-${turnId}`;
    
    // Hide overlay
    const overlay = document.getElementById(`hf-feedback-overlay-${turnId}`);
    if (overlay) {
        overlay.classList.remove('fb-show');
    }
    
    // Command player to restart
    hfCmd(iframeId, 'RESTART');
};

/** Inject animated 👍 / 👎 buttons at the bottom of an AI response card */
function injectFeedbackButtons(turnId) {
    const card = document.getElementById(`ai-card-global-${turnId}`);
    if (!card) {
        console.warn(`[Feedback] Cannot inject row: AI card ${turnId} was not found.`);
        return;
    }
    if (card.querySelector('.fb-row')) return; // already injected
    const row = document.createElement('div');
    row.className = 'fb-row fb-floating';
    row.setAttribute('role', 'dialog');
    row.setAttribute('aria-label', 'Answer feedback');
    row.innerHTML = `
      <span class="fb-label">Was this answer helpful?</span>
      <button class="fb-thumb fb-up" id="fb-up-${turnId}" title="Yes, helpful!"
        onclick="submitFeedback('${turnId}', 'like')">👍</button>
      <button class="fb-thumb fb-down" id="fb-down-${turnId}" title="Could be better"
        onclick="submitFeedback('${turnId}', 'dislike')">👎</button>
    `;
    card.appendChild(row);
    // Slide-in animation trigger
    requestAnimationFrame(() => row.classList.add('fb-visible'));
    console.log(`[Feedback] Rating row rendered for turn ${turnId}.`);

    // Text answers don't get a dismiss/close button the way video lessons
    // do - so this prompt must not linger indefinitely. Auto-dismiss after
    // 5s if the student never answers; submitFeedback() below cancels this
    // and dismisses immediately as soon as they do answer.
    const dismissTimer = setTimeout(() => dismissFeedbackRow(turnId), 5000);
    row.dataset.dismissTimerId = String(dismissTimer);
}

/** Fade out and remove the feedback row for a turn, cancelling any pending auto-dismiss timer. */
function dismissFeedbackRow(turnId) {
    const card = document.getElementById(`ai-card-global-${turnId}`);
    const row = card ? card.querySelector('.fb-row') : null;
    if (!row) return;
    if (row.dataset.dismissTimerId) {
        clearTimeout(Number(row.dataset.dismissTimerId));
    }
    row.classList.remove('fb-visible');
    setTimeout(() => row.remove(), 400); // matches .fb-row's existing 0.4s opacity/transform transition
}

/** Handle a thumbs up or down click */
function submitFeedback(turnId, type) {
    const card = document.getElementById(`ai-card-global-${turnId}`);
    const queryId = (card && card.dataset.queryId) ? card.dataset.queryId : (window.lastQueryId || null);
    const upBtn  = document.getElementById(`fb-up-${turnId}`);
    const downBtn = document.getElementById(`fb-down-${turnId}`);

    // Disable both buttons to prevent double-clicks
    if (upBtn)   upBtn.disabled = true;
    if (downBtn) downBtn.disabled = true;

    if (type === 'like') {
        // Animate the thumbs up
        if (upBtn) { upBtn.classList.add('fb-selected-up'); upBtn.textContent = '👍'; }
        // Show inline thank-you message
        const row = card ? card.querySelector('.fb-row') : null;
        if (row) {
            const thanks = document.createElement('span');
            thanks.className = 'fb-thanks';
            thanks.textContent = '🎉 Thanks! Glad it helped!';
            row.appendChild(thanks);
        }
        // Speak gratitude via TTS
        const msg = new SpeechSynthesisUtterance('Hey, thanks for your feedback! I hope you enjoy learning with me.');
        msg.lang = 'en-IN'; msg.rate = 0.95; msg.pitch = 1.1;
        window.speechSynthesis.cancel();
        window.speechSynthesis.speak(msg);
        // Save to backend silently
        if (queryId) _saveFeedbackToServer(queryId, 'like', '');
        // Once answered, this shouldn't linger on screen - a brief moment
        // for the thank-you message to register, then dismiss.
        setTimeout(() => dismissFeedbackRow(turnId), 1200);
    } else {
        // Animate the thumbs down
        if (downBtn) { downBtn.classList.add('fb-selected-down'); }
        // Open the interactive robot overlay
        openFeedbackOverlay(queryId, turnId);
        // The overlay takes over from here - the underlying row has served
        // its purpose and shouldn't stay visible behind/after it.
        dismissFeedbackRow(turnId);
    }
}

/** Global state for the feedback voice overlay */
let _fbQueryId   = null;
let _fbTurnId    = null;
let _fbTranscript = '';
let _fbRecognition = null;
let _fbSilenceTimer = null;
let _fbMicActive = false;

function ensureFeedbackOverlayMarkup() {
    if (document.getElementById('feedback-dislike-overlay')) return;
    
    const overlay = document.createElement('div');
    overlay.id = 'feedback-dislike-overlay';
    overlay.className = 'fb-overlay';
    overlay.style.display = 'none';
    overlay.innerHTML = `
        <div class="fb-modal">
          <button class="fb-modal-close" aria-label="Close feedback" title="Close" onclick="closeFeedbackOverlay(false)">×</button>
          <div class="fb-robot-wrap">
            <div class="fb-robot-glow"></div>
            <div class="fb-robot-avatar">🤖</div>
          </div>
          <p class="fb-robot-question" id="fb-robot-question">
            Hmm... what could I have done better for you?
          </p>
          <div class="fb-transcript-box">
            <span id="fb-interim-text">🤖 Just a moment...</span>
          </div>
          <div class="fb-actions">
            <button id="fb-mic-btn" class="fb-mic-btn" onclick="fbToggleMic()">🎤 Tap to Speak</button>
            <button class="fb-submit-btn" onclick="closeFeedbackOverlay(true)">✅ Submit</button>
          </div>
          <button class="fb-skip-btn" onclick="closeFeedbackOverlay(false)">✕ Skip</button>
        </div>
    `;
    document.body.appendChild(overlay);
}

/** Open the animated robot dislike overlay and begin the voice flow */
function openFeedbackOverlay(queryId, turnId) {
    ensureFeedbackOverlayMarkup();
    _fbQueryId    = queryId || window.lastQueryId || null;
    _fbTurnId     = turnId;
    _fbTranscript = '';
    _fbMicActive  = false;

    const overlay = document.getElementById('feedback-dislike-overlay');
    const interim = document.getElementById('fb-interim-text');
    const micBtn  = document.getElementById('fb-mic-btn');
    if (interim) interim.textContent = '🤖 Just a moment...';
    if (micBtn)  micBtn.textContent = '🎤 Tap to Speak';
    if (overlay) { overlay.style.display = 'flex'; requestAnimationFrame(() => overlay.classList.add('fb-open')); }

    // Robot speaks its question first, and the student must tap the mic to start recording.
    window.speechSynthesis.cancel();
    const utter = new SpeechSynthesisUtterance("Hmm... what could I have done better for you? Please tap the mic when you are ready to speak.");
    utter.lang = 'en-IN'; utter.rate = 0.92; utter.pitch = 1.05;
    utter.onend = () => {
        const micBtn = document.getElementById('fb-mic-btn');
        if (micBtn) micBtn.textContent = '🎤 Tap to Speak';
    };
    window.speechSynthesis.speak(utter);
}

/** Auto-start the mic (called after robot finishes speaking) */
function fbStartMic() {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRec) return;
    const interim  = document.getElementById('fb-interim-text');
    const micBtn   = document.getElementById('fb-mic-btn');

    if (_fbRecognition) { try { _fbRecognition.stop(); } catch(e) {} }

    _fbRecognition = new SpeechRec();
    _fbRecognition.continuous = true;
    _fbRecognition.interimResults = true;
    _fbRecognition.lang = 'en-IN';
    _fbMicActive = true;
    if (micBtn) micBtn.textContent = '🔴 Listening...';

    _fbRecognition.onresult = (event) => {
        let full = '';
        for (let i = 0; i < event.results.length; i++) {
            full += event.results[i][0].transcript;
        }
        _fbTranscript = full;
        if (interim) interim.textContent = full || '🎙️ Listening...';
        // Reset silence detection
        clearTimeout(_fbSilenceTimer);
        _fbSilenceTimer = setTimeout(() => { closeFeedbackOverlay(true); }, 3500);
    };
    _fbRecognition.onerror = () => { _fbMicActive = false; if (micBtn) micBtn.textContent = '🎤 Tap to Speak'; };
    _fbRecognition.onend   = () => { _fbMicActive = false; if (micBtn) micBtn.textContent = '🎤 Tap to Speak'; };
    _fbRecognition.start();

    // Play a soft beep to signal mic is live
    try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator(); const gain = ctx.createGain();
        osc.connect(gain); gain.connect(ctx.destination);
        osc.type = 'sine'; osc.frequency.value = 880;
        gain.gain.setValueAtTime(0.2, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
        osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.3);
    } catch(e) {}
}

/** Manual mic toggle button (if student wants to control it) */
function fbToggleMic() {
    if (_fbMicActive) {
        if (_fbRecognition) { try { _fbRecognition.stop(); } catch(e) {} }
        _fbMicActive = false;
        const micBtn = document.getElementById('fb-mic-btn');
        if (micBtn) micBtn.textContent = '🎤 Tap to Speak';
    } else {
        fbStartMic();
    }
}

/** Close the overlay — optionally submit the transcribed text */
function closeFeedbackOverlay(shouldSubmit) {
    clearTimeout(_fbSilenceTimer);
    window.speechSynthesis.cancel();
    if (_fbRecognition) { try { _fbRecognition.stop(); } catch(e) {} _fbRecognition = null; }

    const overlay = document.getElementById('feedback-dislike-overlay');
    const interim  = document.getElementById('fb-interim-text');

    if (shouldSubmit && _fbTranscript.trim().length > 0) {
        // Show thank-you message inside overlay before closing
        if (interim) interim.textContent = '💪 Thank you! I\'ll work hard to get better for you!';
        const robotQ = document.getElementById('fb-robot-question');
        if (robotQ) robotQ.textContent = 'Thanks for your honest feedback! 🙏';
        const thankUtter = new SpeechSynthesisUtterance("Thank you! I will work hard to get better for you.");
        thankUtter.lang = 'en-IN'; thankUtter.rate = 0.95; thankUtter.pitch = 1.1;
        window.speechSynthesis.speak(thankUtter);

        // Save to backend
        if (_fbQueryId) _saveFeedbackToServer(_fbQueryId, 'dislike', _fbTranscript.trim());

        // Reflect on the turn card
        if (_fbTurnId) {
            const downBtn = document.getElementById(`fb-down-${_fbTurnId}`);
            if (downBtn) downBtn.classList.add('fb-selected-down');
            const row = document.querySelector(`#ai-card-global-${_fbTurnId} .fb-row`);
            if (row && !row.querySelector('.fb-thanks')) {
                const thanks = document.createElement('span');
                thanks.className = 'fb-thanks fb-thanks-down';
                thanks.textContent = '📝 Feedback noted. Thank you!';
                row.appendChild(thanks);
            }
        }
        // Auto-close after 1.8s
        setTimeout(() => { if (overlay) { overlay.classList.remove('fb-open'); setTimeout(() => { overlay.style.display = 'none'; }, 350); } }, 1800);
    } else {
        // Just close
        if (overlay) { overlay.classList.remove('fb-open'); setTimeout(() => { overlay.style.display = 'none'; }, 350); }
    }
}

/** POST feedback to the backend API */
async function _saveFeedbackToServer(queryId, type, text) {
    try {
        const uid = (window.currentUser && window.currentUser.uid) || null;
        await fetch('/api/feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query_id: queryId, feedback_type: type, feedback_text: text, uid })
        });
        console.log(`[Feedback] Saved '${type}' for query ${queryId}`);
    } catch(e) {
        console.warn('[Feedback] Failed to save feedback:', e);
    }
}

/** Scale the 1280×720 iframe to fill the viewport wrapper using CSS transform */
function hfScaleViewport(wrapperId, scaleBoxId) {
    const wrapper  = document.getElementById(wrapperId);
    const scalebox = document.getElementById(scaleBoxId);
    if (!wrapper || !scalebox) return;

    const isFS = !!(document.fullscreenElement);
    const nativeW = 1280, nativeH = 720;

    if (isFS) {
        const maxW = window.innerWidth;
        const maxH = window.innerHeight - 52; // Subtract control bar height
        const scale = Math.min(maxW / nativeW, maxH / nativeH);
        const scaledH = nativeH * scale;
        
        wrapper.style.height = scaledH + 'px';
        wrapper.style.display = 'flex';
        wrapper.style.justifyContent = 'center';
        wrapper.style.alignItems = 'center';

        scalebox.style.transform = `scale(${scale})`;
        scalebox.style.transformOrigin = 'center center';
        scalebox.style.width  = nativeW + 'px';
        scalebox.style.height = nativeH + 'px';
    } else {
        const containerW = wrapper.getBoundingClientRect().width || wrapper.clientWidth || 800;
        const scale = Math.min(1, containerW / nativeW);
        const scaledH = nativeH * scale;

        wrapper.style.height = scaledH + 'px';
        wrapper.style.display = 'flex';
        wrapper.style.justifyContent = 'flex-start';
        wrapper.style.alignItems = 'flex-start';
        wrapper.style.width = '100%';

        scalebox.style.transform = `scale(${scale})`;
        scalebox.style.transformOrigin = 'top left';
        scalebox.style.width  = nativeW + 'px';
        scalebox.style.height = nativeH + 'px';
    }
}

/** Re-scale all Hyperframes players on window resize or fullscreen change */
window.addEventListener('resize', hfRescaleAllPlayers);
document.addEventListener('fullscreenchange', hfRescaleAllPlayers);

function hfRescaleAllPlayers() {
    document.querySelectorAll('[id^="hf-viewport-"]').forEach(wrapper => {
        const suffix = wrapper.id.replace('hf-viewport-', '');
        hfScaleViewport(`hf-viewport-${suffix}`, `hf-scalebox-${suffix}`);
    });
}

/** Format seconds to M:SS */
function hfFmtTime(seconds) {
    const s = Math.floor(seconds);
    const m = Math.floor(s / 60);
    return `${m}:${String(s % 60).padStart(2, '0')}`;
}

/** Fullscreen the player shell */
function hfFullscreen(playerId) {
    const el = document.getElementById(playerId);
    if (!el) return;
    if (document.fullscreenElement) {
        document.exitFullscreen();
    } else {
        el.requestFullscreen && el.requestFullscreen();
    }
}

/**
 * Sets up the main user query page wizard.
 */
function setupUserPage() {
    // --- Element Selectors ---
    const classSelect = document.getElementById('class-select');
    const subjectSelect = document.getElementById('subject-select');
    const viewerPlaceholder = document.getElementById('viewer-placeholder');
    const pdfLoadingIndicator = document.getElementById('pdf-loading-user');
    const pdfCanvas = document.getElementById('pdf-canvas-user');
    const pdfHeader = document.getElementById('pdf-viewer-header-user');
    const pageNumEl = document.getElementById('page-num-user');
    const pageCountEl = document.getElementById('page-count-user');
    const prevPageBtn = document.getElementById('prev-page-user');
    const nextPageBtn = document.getElementById('next-page-user');
    const chatHistory = document.getElementById('chat-history') || document.getElementById('chat-container');
    const queryForm = document.getElementById('user-query-form');
    const queryText = document.getElementById('query-text');
    const submitButton = document.getElementById('submit-query-btn');
    const listChaptersBtn = document.getElementById('list-chapters-btn');
    const conversationalModeBtn = document.getElementById('conversational-mode-btn');
    const voiceSearchBtn = document.getElementById('voice-search-btn');
    const voiceStatus = document.getElementById('voice-status');
    const voiceVisualizer = document.getElementById('voice-visualizer');
    let hideVoiceVisualizerTimeout = null;
    const ctx = pdfCanvas.getContext('2d');

    // --- App State ---
    let selectedBook = null;
    let pdfDoc = null;
    let pageNum = 1;
    let pageRendering = false;
    let pageNumPending = null;
    let isFirstQuery = true;
    // Removed isSpeakingStream and sentenceQueue

    // --- Smart Conversational Context State ---
    let currentSessionId = null;
    let turnCount = 0;
    let currentFollowUps = [];

    // --- Voice Search State (simple mode) ---
    let simpleRecognition;
    let isSimpleRecording = false;

    // --- Initialization ---
    setupSimpleVoiceSearch();
    createFollowupVoiceOverlay();

    // --- Event Listeners ---
    if (classSelect) {
        classSelect.addEventListener('change', () => {
            const selectedClass = classSelect.value;
            if (selectedClass) {
                populateSubjects(selectedClass);
            } else {
                subjectSelect.innerHTML = '<option value="">Select Subject</option>';
                subjectSelect.disabled = true;
            }
            resetUI();
        });
    }

    if (subjectSelect) {
        subjectSelect.addEventListener('change', () => loadBook());
    }
    if (queryForm) {
        queryForm.addEventListener('submit', (e) => {
            e.preventDefault();
            handleQuerySubmit();
        });
    }
    if (listChaptersBtn) {
        listChaptersBtn.addEventListener('click', () => handleListChapters());
    }

    // Page navigation buttons (Previous and Next only)
    const pageInput = document.getElementById('page-input-user');

    prevPageBtn.addEventListener('click', () => {
        if (pageNum <= 1) return;
        pageNum--;
        queueRenderPage(pageNum);
    });

    nextPageBtn.addEventListener('click', () => {
        if (pdfDoc && pageNum >= pdfDoc.numPages) return;
        pageNum++;
        queueRenderPage(pageNum);
    });

    // Page input - update on change or blur
    if (pageInput) {
        pageInput.addEventListener('change', () => jumpToPageInput());
        pageInput.addEventListener('blur', () => jumpToPageInput());
    }
    queryText.addEventListener('input', () => {
        queryText.style.height = 'auto';
        queryText.style.height = (queryText.scrollHeight) + 'px';
    });

    // --- Voice Search Setup ---
    function setupSimpleVoiceSearch() {
        if (!voiceSearchBtn) return;
        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        if (!SpeechRecognition) {
            voiceSearchBtn.disabled = true;
            voiceSearchBtn.title = "Voice search not supported";
            return;
        }

        simpleRecognition = new SpeechRecognition();
        simpleRecognition.interimResults = true;
        simpleRecognition.lang = 'en-US';

        const scheduleFrame = typeof window !== 'undefined' && window.requestAnimationFrame
            ? window.requestAnimationFrame.bind(window)
            : (cb) => setTimeout(cb, 16);

        function showVoiceVisualizer() {
            if (!voiceVisualizer) return;
            if (hideVoiceVisualizerTimeout) {
                clearTimeout(hideVoiceVisualizerTimeout);
                hideVoiceVisualizerTimeout = null;
            }
            voiceVisualizer.classList.remove('hidden');
            scheduleFrame(() => voiceVisualizer.classList.add('active'));
        }

        function hideVoiceVisualizer() {
            if (!voiceVisualizer) return;
            voiceVisualizer.classList.remove('active');
            hideVoiceVisualizerTimeout = window.setTimeout(() => {
                voiceVisualizer.classList.add('hidden');
            }, 220);
        }

        simpleRecognition.onstart = () => {
            isSimpleRecording = true;
            voiceStatus.textContent = 'Recording...';
            voiceStatus.classList.remove('hidden');
            voiceSearchBtn.classList.remove('bg-gray-200', 'hover:bg-gray-300');
            voiceSearchBtn.classList.add('bg-red-500', 'hover:bg-red-600');
            voiceSearchBtn.classList.add('recording');
            showVoiceVisualizer();
        };

        simpleRecognition.onend = () => {
            isSimpleRecording = false;
            voiceStatus.classList.add('hidden');
            voiceSearchBtn.classList.remove('bg-red-500', 'hover:bg-red-600');
            voiceSearchBtn.classList.add('bg-gray-200', 'hover:bg-gray-300');
            voiceSearchBtn.classList.remove('recording');
            hideVoiceVisualizer();
        };

        simpleRecognition.onerror = (event) => {
            console.error('Speech recognition error:', event.error);
            voiceStatus.textContent = `Error: ${event.error}`;
            isSimpleRecording = false;
            voiceStatus.classList.remove('hidden');
            voiceSearchBtn.classList.remove('bg-red-500', 'hover:bg-red-600');
            voiceSearchBtn.classList.add('bg-gray-200', 'hover:bg-gray-300');
            voiceSearchBtn.classList.remove('recording');
            hideVoiceVisualizer();
        };

        simpleRecognition.onresult = (event) => {
            let interimTranscript = '';
            let finalTranscript = '';

            for (let i = event.resultIndex; i < event.results.length; ++i) {
                if (event.results[i].isFinal) {
                    finalTranscript += event.results[i][0].transcript;
                } else {
                    interimTranscript += event.results[i][0].transcript;
                }
            }
            // Update the queryText with both final and interim results for live feedback
            queryText.value = finalTranscript + interimTranscript;
        };

        voiceSearchBtn.addEventListener('click', () => {
            if (isSimpleRecording) {
                simpleRecognition.stop();
            } else {
                // Prevent conflict with conversational mode
                if (window.conversationMode && window.conversationMode.isRecording) {
                    alert("Please stop the conversational mode first.");
                    return;
                }
                try {
                    simpleRecognition.start();
                } catch (e) {
                    console.error("Could not start recognition:", e);
                    voiceStatus.textContent = "Mic error.";
                    voiceStatus.classList.remove('hidden');
                }
            }
        });
    }

    // --- Core Functions ---
    async function populateSubjects(className) {
        try {
            console.log('[PopulateSubjects] Fetching subjects for class:', className);

            // Use new centralized subject configuration API
            const response = await fetch(`/api/subjects?class_name=${className}`);
            if (!response.ok) throw new Error('Failed to fetch subjects');

            const data = await response.json();
            const subjects = data.subjects || [];

            console.log('[PopulateSubjects] Received subjects:', subjects);

            // Clear and repopulate subject dropdown
            subjectSelect.innerHTML = '<option value="">Select Subject</option>';

            subjects.forEach(subjectData => {
                const option = document.createElement('option');
                option.value = subjectData.name;
                option.textContent = `${subjectData.icon} ${subjectData.display_name}`;
                subjectSelect.appendChild(option);
            });

            subjectSelect.disabled = false;
            console.log('[PopulateSubjects] ✓ Subject dropdown populated successfully');
        } catch (error) {
            console.error('Error fetching subjects:', error);
            // Fallback to basic subjects
            subjectSelect.innerHTML = '<option value="">Select Subject</option>' +
                '<option value="english">📖 English</option>' +
                '<option value="maths">🔢 Maths</option>' +
                '<option value="science">🔬 Science</option>' +
                '<option value="social">🌍 Social</option>';
        }
    }
    window.populateSubjectsForUser = populateSubjects;

    function resetUI() {
        pdfDoc = null;
        selectedBook = null;
        pageNum = 1;
        pdfCanvas.style.display = 'none';
        pdfHeader.style.display = 'none';
        viewerPlaceholder.style.display = 'flex';
        pdfLoadingIndicator.style.display = 'none';
        queryText.disabled = true;
        submitButton.disabled = true;
        listChaptersBtn.classList.add('hidden');
        if (conversationalModeBtn) {
            conversationalModeBtn.disabled = true;
            conversationalModeBtn.classList.add('opacity-50', 'cursor-not-allowed');
        }
        queryText.placeholder = 'Ask a question about the selected book...';
    }

    async function loadBook() {
        const className = String(window.currentUserClass || localStorage.getItem('userClass') || '').replace(/\D/g, '');
        const subject = subjectSelect.value;
        if (!className || !subject) return;

        resetUI();
        viewerPlaceholder.style.display = 'none';
        pdfLoadingIndicator.style.display = 'flex';

        try {
            const response = await fetch(`/api/books?class_name=${className}&subject=${subject}`);
            if (!response.ok) throw new Error('Book not found.');
            const books = await response.json();
            if (books.length === 0) throw new Error('Book not found for this selection.');

            selectedBook = books[0];
            window.selectedBook = selectedBook;

            queryText.disabled = false;
            submitButton.disabled = false;
            if (voiceSearchBtn) voiceSearchBtn.disabled = false;  // Enable voice button
            listChaptersBtn.classList.remove('hidden');
            if (conversationalModeBtn) {
                conversationalModeBtn.disabled = false;
                conversationalModeBtn.classList.remove('opacity-50', 'cursor-not-allowed');
            }

            const pdfUrl = `/uploads/${selectedBook.filename}`;
            pdfDoc = await pdfjsLib.getDocument(pdfUrl).promise;

            pdfLoadingIndicator.style.display = 'none';
            pdfCanvas.style.display = 'block';
            pdfHeader.style.display = 'flex';
            pageCountEl.textContent = pdfDoc.numPages;
            renderPage(pageNum);

            chatHistory.innerHTML = '';
            isFirstQuery = false;

            // Reset session when book changes
            currentSessionId = null;
            turnCount = 0;
            currentFollowUps = [];

            // appendAIResponse now handles speech based on isSpeechEnabledByDefault
            appendAIResponse(`Book "${selectedBook.subject}" loaded. You can now ask questions about it.`, `Book ${selectedBook.subject} loaded. You can now ask questions about it.`);

        } catch (error) {
            pdfLoadingIndicator.style.display = 'none';
            viewerPlaceholder.style.display = 'flex';
            viewerPlaceholder.innerHTML = `<p class="error-message">${error.message}</p>`;
            console.error(error);
        }
    }

    async function renderPage(num) {
        pageRendering = true;
        pdfLoadingIndicator.style.display = 'flex';
        const page = await pdfDoc.getPage(num);
        const container = document.getElementById('pdf-render-area-user');
        const viewport = page.getViewport({ scale: container.clientWidth / page.getViewport({ scale: 1 }).width });
        const outputScale = window.devicePixelRatio || 1;

        pdfCanvas.width = Math.floor(viewport.width * outputScale);
        pdfCanvas.height = Math.floor(viewport.height * outputScale);
        pdfCanvas.style.width = Math.floor(viewport.width) + 'px';
        pdfCanvas.style.height = Math.floor(viewport.height) + 'px';

        const renderContext = {
            canvasContext: ctx,
            viewport: viewport,
            transform: [outputScale, 0, 0, outputScale, 0, 0]
        };
        const renderTask = page.render(renderContext);
        await renderTask.promise;
        pageRendering = false;
        pdfLoadingIndicator.style.display = 'none';
        if (pageNumPending !== null) {
            renderPage(pageNumPending);
            pageNumPending = null;
        }

        // Update page number display and input
        const pageInput = document.getElementById('page-input-user');
        if (pageInput) {
            pageInput.value = num;
        }
    }

    /**
     * Jump to page entered in the input field
     */
    function jumpToPageInput() {
        const pageInput = document.getElementById('page-input-user');
        if (!pageInput || !pdfDoc) return;

        const targetPage = parseInt(pageInput.value, 10);

        // Validate page number
        if (isNaN(targetPage) || targetPage < 1 || targetPage > pdfDoc.numPages) {
            // Reset to current page if invalid
            pageInput.value = pageNum;
            return;
        }

        // Navigate to the page
        if (targetPage !== pageNum) {
            pageNum = targetPage;
            queueRenderPage(pageNum);
        }
    }

    // Make jumpToPageInput globally accessible for inline onkeypress
    window.jumpToPageInput = jumpToPageInput;

    /**
     * If another page rendering in progress, waits until the rendering is
     * finished. Otherwise, executes rendering immediately.
     */
    function queueRenderPage(num) {
        if (pageRendering) {
            pageNumPending = num;
        } else {
            renderPage(num);
        }
    }

    function addUserMessage(text) {
        const row = document.createElement('div');
        row.className = 'chat-bubble-row chat-bubble-row--user';
        const now = new Date();
        const timeStr = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

        const escaped = (text || '').replace(/[&<>'"]/g, 
            tag => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[tag] || tag)
        );

        row.innerHTML = `
            <div class="user-bubble-card">
                <div>${escaped}</div>
                <div class="user-bubble-meta">${timeStr}</div>
            </div>
        `;
        chatHistory.appendChild(row);
        chatHistory.scrollTop = chatHistory.scrollHeight;
    }

    async function handleQuerySubmit() {
        const query = queryText.value.trim();
        if (!query) return;

        // All questions go through the orchestrator (submitSmartQuery), which
        // decides text vs video itself based on content - there is no longer a
        // separate "Visual Learning Mode" bypass. The old direct-to-VisualLearningRenderer
        // path targeted DOM elements that no longer exist in this page and was dead.
        await window.submitSmartQuery(query, false);
    }

    /**
     * Create AI Message Card with Turn Counter and Intent Badge
     */
    function createAIMessageCard(turnNumber, initialIntent = 'loading') {
        const messageDiv = document.createElement("div");
        messageDiv.className = "ai-card fade-in";

        const isHiddenMode = window.answerPreferenceManager && 
            (window.answerPreferenceManager.currentMode === 'text_text' || 
             window.answerPreferenceManager.currentMode === 'text_audio' || 
             window.answerPreferenceManager.currentMode === 'audio_text' ||
             window.answerPreferenceManager.currentMode === 'audio_audio');
        const speakBtnStyle = isHiddenMode ? 'display: none;' : '';

        const header = `
            <div class="ai-card-header">
                <div class="flex items-center gap-2">
                    <h2 class="font-semibold text-gray-700">🤖 AI Response</h2>
                    <span class="intent-badge ${initialIntent}" style="display: none;"></span>
                </div>
                <div class="flex items-center gap-2">
                    <span class="turn-indicator">Turn ${turnNumber}</span>
                    <button class="copy-btn" onclick="copyMessage(this)" title="Copy">📋</button>
                    <button class="save-bag-btn" onclick="saveToBag(this)" title="Save to Bag" style="background:none; border:none; cursor:pointer; font-size:1.1rem; margin-left:4px;">🎒</button>
                    <button class="speak-btn" onclick="speakMessage(this)" title="Read Aloud" style="${speakBtnStyle}">🔊</button>
                </div>
            </div>
            <div class="markdown-content"></div>
            <div class="followup-section" style="display: none;"></div>
        `;

        messageDiv.innerHTML = header;
        return messageDiv;
    }

    /**
     * Update Intent Badge when intent is received from backend
     */
    function updateIntentBadge(cardElement, intentType) {
        const badge = cardElement.querySelector('.intent-badge');
        if (!badge) return;

        badge.style.display = 'inline-flex';
        badge.className = `intent-badge ${intentType}`;

        if (intentType === 'followup') {
            badge.innerHTML = '🔄 Follow-up';
        } else if (intentType === 'independent') {
            badge.innerHTML = '✨ New Topic';
        }
    }

    /**
     * Add Follow-up Suggestions UI to AI Card
     */
    function addFollowUpsUI(cardElement, followups) {
        if (!followups || followups.length === 0) return;

        const followupSection = cardElement.querySelector('.followup-section');
        if (!followupSection) return;

        // Show section
        followupSection.style.display = 'block';

        // Fun emoji array
        const emojis = ['💡', '🤔', '🔍', '⭐', '🎯', '💭', '🌟', '✨'];

        let html = `
            <div class="followup-header">
                <span class="icon">💡</span>
                <h4>Quick Follow-ups</h4>
            </div>
            <div class="followup-chips-container">
        `;

        // Add follow-up chips
        followups.forEach((followup, index) => {
            const escapedFollowup = followup.replace(/'/g, "\\'");
            const emoji = emojis[index % emojis.length];
            html += `
                <button class="followup-chip" onclick="handleFollowupClick('${escapedFollowup}')">
                    <span class="followup-chip-icon">${emoji}</span>
                    <span class="followup-chip-text">${followup}</span>
                </button>
            `;
        });

        html += `</div>`;

        // Removed custom input field as per user request (redundant with main chat)

        followupSection.innerHTML = html;

        // Hide sticky panel if it exists (cleanup)
        const stickyPanel = document.getElementById('followup-sticky-panel');
        if (stickyPanel) {
            stickyPanel.classList.add('hidden');
        }
    }

    // Handle sticky panel input
    window.handleStickyFollowup = function (input) {
        const question = input.value.trim();
        if (!question) return;

        const queryText = document.getElementById('query-text');
        if (!queryText) return;

        queryText.value = question;
        input.value = '';

        // Trigger form submission
        const form = document.getElementById('user-query-form');
        if (form) {
            const event = new Event('submit', { bubbles: true, cancelable: true });
            form.dispatchEvent(event);
        }
    };

    // Toggle sticky panel collapsed state
    window.toggleFollowupPanel = function () {
        const panel = document.getElementById('followup-sticky-panel');
        if (panel) {
            panel.classList.toggle('collapsed');
        }
    };


    async function handleListChapters() {
        if (!selectedBook) return;

        if (isFirstQuery) {
            chatHistory.innerHTML = '';
            isFirstQuery = false;
        }

        addUserMessage('List all chapters');
        const thinkingMessage = appendAIResponse('Fetching chapters...', 'Fetching chapters'); // Pass initial read text as well

        submitButton.setAttribute('disabled', 'true');
        listChaptersBtn.classList.add('hidden');

        try {
            const className = classSelect.value;
            const subject = subjectSelect.value;
            const response = await fetch(`/api/list-chapters?class_name=${className}&subject=${subject}`);

            if (!response.ok) {
                const errorResult = await response.json();
                throw new Error(errorResult.detail || 'Failed to get chapters.');
            }

            const result = await response.json();
            let chapters = result.chapters;

            if (!chapters || chapters.length === 0) {
                throw new Error("No chapters were found for this book in the database.");
            }

            chapters.sort((a, b) => a.start_page - b.start_page);

            let tableMd = `
| S.No. | Chapter Name | Pages |
|---|---|---|
`;
            chapters.forEach((chapter, index) => {
                tableMd += `| ${index + 1} | ${chapter.name} | ${chapter.start_page} - ${chapter.end_page} |\n`;
            });

            const formatted = marked.parse(tableMd);
            thinkingMessage.querySelector('.markdown-content').innerHTML = formatted;
            // Speak a short confirmation via ttsManager
            if (window.ttsManager) {
                window.ttsManager.speak('Here are the chapters.');
            } else {
                speechSynthesis.cancel();
                speechSynthesis.speak(new SpeechSynthesisUtterance('Here are the chapters.'));
            }

        } catch (error) {
            thinkingMessage.querySelector('.markdown-content').innerHTML = `<p style="color: red;"><strong>Error:</strong> ${error.message}</p>`;
            if (window.ttsManager) {
                window.ttsManager.speak(`Sorry, an error occurred: ${error.message}`);
            } else {
                speechSynthesis.cancel();
                speechSynthesis.speak(new SpeechSynthesisUtterance(`Sorry, an error occurred: ${error.message}`));
            }
        } finally {
            submitButton.removeAttribute('disabled');
            listChaptersBtn.classList.remove('hidden');
            chatHistory.scrollTop = chatHistory.scrollHeight;
        }
    }

    // ── Answer Preference: global mic-query bridge ────────────────────────────
    // Called by AnswerPreferenceManager when the preference mic finishes
    // transcribing. Places the transcript into the query pipeline exactly as
    // if the user had typed it and clicked Send.
    window.submitSmartQueryFromMic = function(transcript) {
        console.log('[MODE] submitSmartQueryFromMic called with:', transcript);
        if (!transcript) return;
        // Same unified path as handleQuerySubmit - no more Visual Learning Mode bypass.
        submitSmartQuery(transcript, false);
    };
}

/**
 * Global Helper Functions for Smart Follow-ups
 */

// Toggle follow-up suggestions panel
window.toggleFollowups = function (header) {
    const chips = header.nextElementSibling;
    const icon = header.querySelector('.toggle-icon');

    if (chips.style.display === 'none') {
        chips.style.display = 'flex';
        icon.textContent = '▼';
        icon.classList.remove('collapsed');
    } else {
        chips.style.display = 'none';
        icon.textContent = '▶';
        icon.classList.add('collapsed');
    }
};

// Handle follow-up chip click
window.handleFollowupClick = async function (question) {
    const queryText = document.getElementById('query-text');
    if (!queryText) return;

    // Get the submitSmartQuery function from the setupUserPage scope
    // We need to trigger a smart query with isClickedFollowup=true
    queryText.value = question;

    // Find the user page's submit handler
    const form = document.getElementById('user-query-form');
    if (form) {
        // Programmatically trigger the form submission
        // which calls handleQuerySubmit -> submitSmartQuery
        const event = new Event('submit', { bubbles: true, cancelable: true });
        form.dispatchEvent(event);
    }
};

// Handle inline follow-up input
window.handleInlineFollowup = function (input) {
    const question = input.value.trim();
    if (!question) return;

    const queryText = document.getElementById('query-text');
    if (!queryText) return;

    queryText.value = question;
    input.value = '';

    // Trigger form submission
    const form = document.getElementById('user-query-form');
    if (form) {
        const event = new Event('submit', { bubbles: true, cancelable: true });
        form.dispatchEvent(event);
    }
};


/**
 * Utility to show status messages to the user.
 * This function is kept for other pages but is not used in the new user page wizard.
 * A more integrated status/error display is used instead.
 */
function showStatus(message, type) {
    const statusContainer = document.getElementById('status-container');
    if (statusContainer) {
        statusContainer.textContent = message;
        statusContainer.className = `status-message ${type}`;
        statusContainer.style.display = 'block';
    }
}

// Modified appendAIResponse to take both display and read text, and handle speech
function appendAIResponse(displayText, readText = '') {
    const chatHistory = document.getElementById("chat-history");
    const messageDiv = document.createElement("div");
    messageDiv.className = "ai-card fade-in";

    const isHiddenMode = window.answerPreferenceManager && 
        (window.answerPreferenceManager.currentMode === 'text_text' || 
         window.answerPreferenceManager.currentMode === 'text_audio' || 
         window.answerPreferenceManager.currentMode === 'audio_text' ||
         window.answerPreferenceManager.currentMode === 'audio_audio');
    const speakBtnStyle = isHiddenMode ? 'display: none;' : '';

    const header = `
        <div class="flex justify-between items-center mb-2">
          <h2 class="font-semibold text-gray-700">🤖 AI Response</h2>
          <div>
            <button class="copy-btn" onclick="copyMessage(this)" title="Copy">📋</button>
            <button class="save-bag-btn" onclick="saveToBag(this)" title="Save to Bag" style="background:none; border:none; cursor:pointer; font-size:1.1rem; margin-left:4px;">🎒</button>
            <button class="speak-btn" onclick="speakMessage(this)" title="Read Aloud" style="${speakBtnStyle}">🔊</button>
          </div>
        </div>`;

    const formatted = marked.parse(displayText);
    messageDiv.innerHTML = header + `<div class="markdown-content">${formatted}</div>`;
    chatHistory.appendChild(messageDiv);
    chatHistory.scrollTop = chatHistory.scrollHeight;

    // On-demand only — user clicks 🔊. No auto-play.
    return messageDiv; // Return the element
}

function copyMessage(btn) {
    const text = btn.closest(".ai-card").querySelector(".markdown-content").innerText;
    navigator.clipboard.writeText(text);
    btn.textContent = "✅";
    setTimeout(() => (btn.textContent = "📋"), 1200);
}

// Save to Bag Handler
window.saveToBag = function (btn) {
    const text = btn.closest(".ai-card").querySelector(".markdown-content").innerText;
    if (window.myBag && typeof window.myBag.saveFromChat === 'function') {
        // Use the instance exposed in my-bag.js? 
        // Wait, my-bag.js exposes 'myBag' as a const, but it's not on window.
        // But it exposes window.openBag.
        // I should update my-bag.js to expose the instance or a helper.
        // For now, I'll assume I can access the class or I need to update my-bag.js.
        // Actually, I can just dispatch a custom event or use the global openBag to trigger something?
        // No, I need to call saveFromChat.

        // Let's check if myBag is available.
        // In my-bag.js I did: const myBag = new MyBag();
        // It is NOT attached to window.

        // I will fix my-bag.js to attach it to window.
        console.error("MyBag instance not found on window. Please update my-bag.js");
    } else if (window.openBag) {
        // Fallback if myBag instance isn't directly exposed but openBag is.
        // This implies my-bag.js is loaded.
        // I will assume I will fix my-bag.js in the next step to expose window.myBag
        window.myBag.saveFromChat(text);
    } else {
        alert("My Bag feature is not ready yet.");
    }
}


// speakMessage — called by the 🔊 button on every AI card
// Toggles: click to speak → click again to stop → click again to repeat
window.speakMessage = function (button) {
    const card    = button.closest('.ai-card');
    const content = card ? card.querySelector('.markdown-content').innerText : '';

    // Intercept in Tutor Mode or AI Voice Mode when the streaming pipeline is active
    const isStreamActive = window.ttsPipeline && window.ttsPipeline.isActive;
    if (window.answerPreferenceManager && 
        (window.answerPreferenceManager.currentMode === 'text_audio' || window.answerPreferenceManager.currentMode === 'audio_audio') && 
        isStreamActive) {
        if (window.playbackController) {
            if (window.playbackController.isPaused) {
                window.playbackController.resumePipeline();
            } else {
                window.playbackController.pausePipeline();
            }
        }
        return;
    }

    if (!window.playbackController) {
        // Safe fallback if playbackController hasn't loaded yet
        if (window.ttsManager) {
            if (window.ttsManager.isSpeaking) {
                window.ttsManager.stop();
                document.querySelectorAll('.speak-btn').forEach(btn => btn.textContent = '🔊');
            } else {
                document.querySelectorAll('.speak-btn').forEach(btn => btn.textContent = '🔊');
                window.ttsManager.speak(content, button);
            }
        }
        return;
    }

    if (window.playbackController.currentEngine === 'manager' && window.playbackController.currentNarrationId === button) {
        if (window.playbackController.isPaused) {
            window.playbackController.resumeManager();
        } else {
            window.playbackController.pauseManager();
        }
    } else {
        // Different button clicked or not speaking -> start
        window.playbackController.startManager(content, button);
    }
}

/**
 * =====================================================
 * VOICE INPUT FOR FOLLOW-UP QUESTIONS
 * =====================================================
 */

// Global state for followup voice recognition
let followupVoiceRecognition = null;
let followupVoiceTranscript = '';
let isFollowupVoiceActive = false;

// Create voice overlay for follow-up recording
function createFollowupVoiceOverlay() {
    const overlay = document.createElement('div');
    overlay.id = 'followup-voice-overlay';
    overlay.className = 'followup-voice-overlay';
    overlay.innerHTML = `
        <div class="followup-voice-content">
            <h3>🎤 Speak Your Follow-up Question</h3>
            <div class="followup-voice-animation">
                <div class="followup-voice-bar"></div>
                <div class="followup-voice-bar"></div>
                <div class="followup-voice-bar"></div>
                <div class="followup-voice-bar"></div>
                <div class="followup-voice-bar"></div>
            </div>
            <div class="followup-voice-transcript" id="followup-voice-transcript">
                Listening...
            </div>
            <div class="followup-voice-actions">
                <button class="followup-voice-cancel" onclick="cancelFollowupVoice()">
                    ✕ Cancel
                </button>
                <button class="followup-voice-submit" id="followup-voice-submit" onclick="submitFollowupVoice()" disabled>
                    ✓ Submit
                </button>
            </div>
        </div>
    `;
    document.body.appendChild(overlay);
}

// Initialize speech recognition for follow-ups
function initFollowupVoiceRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
        alert('Voice recognition is not supported in your browser. Please use Chrome, Edge, or Safari.');
        return null;
    }

    const recognition = new SpeechRecognition();
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.lang = 'en-US';

    recognition.onstart = () => {
        console.log('[FollowupVoice] Recognition started');
        isFollowupVoiceActive = true;
    };

    recognition.onresult = (event) => {
        let interimTranscript = '';
        let finalTranscript = '';

        for (let i = event.resultIndex; i < event.results.length; i++) {
            const transcript = event.results[i][0].transcript;
            if (event.results[i].isFinal) {
                finalTranscript += transcript + ' ';
            } else {
                interimTranscript += transcript;
            }
        }

        // Update global transcript
        if (finalTranscript) {
            followupVoiceTranscript = (followupVoiceTranscript + ' ' + finalTranscript).trim();
        }

        // Display transcript
        const transcriptEl = document.getElementById('followup-voice-transcript');
        if (transcriptEl) {
            const displayText = followupVoiceTranscript + (interimTranscript ? ' ' + interimTranscript : '');
            transcriptEl.textContent = displayText || 'Listening...';

            // Enable submit button if we have text
            const submitBtn = document.getElementById('followup-voice-submit');
            if (submitBtn) {
                submitBtn.disabled = !followupVoiceTranscript.trim();
            }
        }
    };

    recognition.onerror = (event) => {
        console.error('[FollowupVoice] Recognition error:', event.error);
        const transcriptEl = document.getElementById('followup-voice-transcript');
        if (transcriptEl) {
            transcriptEl.textContent = `Error: ${event.error}. Please try again.`;
            transcriptEl.style.color = '#ef4444';
        }
    };

    recognition.onend = () => {
        console.log('[FollowupVoice] Recognition ended');
        isFollowupVoiceActive = false;
    };

    return recognition;
}

// Handle voice button click for custom follow-up input
window.handleCustomFollowupVoice = function () {
    console.log('[FollowupVoice] Starting voice input for custom follow-up');

    // Reset transcript
    followupVoiceTranscript = '';

    // Initialize recognition if needed
    if (!followupVoiceRecognition) {
        followupVoiceRecognition = initFollowupVoiceRecognition();
        if (!followupVoiceRecognition) return; // Not supported
    }

    // Show overlay
    const overlay = document.getElementById('followup-voice-overlay');
    if (overlay) {
        overlay.classList.add('active');

        // Reset UI
        const transcriptEl = document.getElementById('followup-voice-transcript');
        if (transcriptEl) {
            transcriptEl.textContent = 'Listening...';
            transcriptEl.style.color = '#374151';
        }

        const submitBtn = document.getElementById('followup-voice-submit');
        if (submitBtn) {
            submitBtn.disabled = true;
        }
    }

    // Start recognition
    try {
        followupVoiceRecognition.start();
    } catch (e) {
        console.error('[FollowupVoice] Failed to start recognition:', e);
        // If already running, stop and restart
        followupVoiceRecognition.stop();
        setTimeout(() => {
            try {
                followupVoiceRecognition.start();
            } catch (err) {
                console.error('[FollowupVoice] Failed to restart recognition:', err);
                alert('Could not start voice recognition. Please try again.');
                closeFollowupVoiceOverlay();
            }
        }, 300);
    }
};

// Cancel voice input
window.cancelFollowupVoice = function () {
    console.log('[FollowupVoice] Canceling voice input');

    if (followupVoiceRecognition && isFollowupVoiceActive) {
        followupVoiceRecognition.stop();
    }

    closeFollowupVoiceOverlay();
    followupVoiceTranscript = '';
};

// Submit voice input as follow-up query
window.submitFollowupVoice = function () {
    console.log('[FollowupVoice] Submitting voice input:', followupVoiceTranscript);

    if (!followupVoiceTranscript.trim()) {
        alert('No speech detected. Please try again.');
        return;
    }

    // Stop recognition
    if (followupVoiceRecognition && isFollowupVoiceActive) {
        followupVoiceRecognition.stop();
    }

    // Close overlay
    closeFollowupVoiceOverlay();

    // Submit as follow-up query
    const queryText = document.getElementById('query-text');
    if (queryText) {
        queryText.value = followupVoiceTranscript;

        // Trigger form submission
        const form = document.getElementById('user-query-form');
        if (form) {
            const event = new Event('submit', { bubbles: true, cancelable: true });
            form.dispatchEvent(event);
        }
    }

    // Reset transcript
    followupVoiceTranscript = '';
};

// Helper to close overlay
function closeFollowupVoiceOverlay() {
    const overlay = document.getElementById('followup-voice-overlay');
    if (overlay) {
        overlay.classList.remove('active');
    }
}

// Central Playback Controller Subscriber to synchronize AI Card button icons
document.addEventListener('DOMContentLoaded', () => {
    if (window.playbackController) {
        window.playbackController.subscribe((state) => {
            const activeBtn = state.currentNarrationId;
            
            // Query all speak buttons in the DOM
            const allSpeakBtns = document.querySelectorAll('.speak-btn');
            
            allSpeakBtns.forEach(btn => {
                if (activeBtn && btn === activeBtn) {
                    btn.style.display = ''; // Ensure active button is visible
                    if (state.playbackStatus === 'speaking') {
                        btn.textContent = '⏸';
                        btn.title = 'Pause Narration';
                    } else if (state.playbackStatus === 'paused') {
                        btn.textContent = '▶';
                        btn.title = 'Resume Narration';
                    } else {
                        btn.textContent = '🔊';
                        btn.title = 'Read Aloud';
                    }
                } else {
                    // All other buttons return to speaker icon
                    btn.textContent = '🔊';
                    btn.title = 'Read Aloud';
                }
            });
        });
    }
});

// Global registry for chat player states
window.chatPlayers = {};

window.showVideoProgressUI = function(cardElement, data) {
    let progressContainer = cardElement.querySelector('.vl-progress-container');
    if (!progressContainer) {
        progressContainer = document.createElement('div');
        progressContainer.className = 'vl-progress-container';
        progressContainer.style.marginTop = '12px';
        progressContainer.style.padding = '12px';
        progressContainer.style.background = 'rgba(255,255,255,0.05)';
        progressContainer.style.borderRadius = '8px';
        progressContainer.style.border = '1px solid rgba(255,255,255,0.1)';
        progressContainer.innerHTML = `
            <div style="display:flex; justify-content:space-between; margin-bottom:6px; font-size:12px; color:#94a3b8;">
                <span class="progress-step-msg">Preparing lesson generation...</span>
                <span class="progress-pct">10%</span>
            </div>
            <div style="width:100%; height:6px; background:#1e293b; border-radius:3px; overflow:hidden;">
                <div class="progress-bar-fill" style="width:10%; height:100%; background:#10b981; transition:width 0.4s ease;"></div>
            </div>
        `;
        const contentDiv = cardElement.querySelector('.markdown-content');
        if (contentDiv) {
            contentDiv.after(progressContainer);
        } else {
            cardElement.appendChild(progressContainer);
        }
    }

    const step = data.step || '';
    const message = data.message || 'Creating your visual lesson...';
    let pct = '10%';
    if (step === 'understanding_topic') pct = '15%';
    else if (step === 'designing_lesson') pct = '35%';
    else if (step === 'generating_visuals') pct = '55%';
    else if (step === 'creating_narration') pct = '75%';
    else if (step === 'hyperframes_engine') pct = '90%';
    else if (step === 'launching_lesson') pct = '100%';

    const stepMsg = progressContainer.querySelector('.progress-step-msg');
    const progressPct = progressContainer.querySelector('.progress-pct');
    const barFill = progressContainer.querySelector('.progress-bar-fill');

    if (stepMsg) stepMsg.textContent = message;
    if (progressPct) progressPct.textContent = pct;
    if (barFill) barFill.style.width = pct;
};

window.renderCustomVideoPlayer = function(cardElement, data) {
    // Remove progress bar if exists
    const progressContainer = cardElement.querySelector('.vl-progress-container');
    if (progressContainer) {
        progressContainer.remove();
    }

    const htmlUrl = data.html_url || data.interactive_url;
    // Extract lessonId from htmlUrl
    let lessonId = 'vl_unknown';
    if (htmlUrl) {
        const parts = htmlUrl.split('/');
        if (parts.length >= 3) {
            lessonId = parts[parts.length - 2];
        }
    }
    
    // Check if player is already mounted
    if (cardElement.querySelector(`.custom-youtube-player`)) return;

    const playerDiv = document.createElement('div');
    playerDiv.className = 'custom-youtube-player';

    // Add state tracker
    window.chatPlayers[lessonId] = {
        currentTime: 0,
        duration: 0,
        isPlaying: true
    };

    playerDiv.innerHTML = `
        <iframe class="custom-player-iframe" id="vl-chat-iframe-${lessonId}" src="${htmlUrl}"></iframe>
        <div class="custom-player-controls">
            <div class="custom-player-left-group">
                <button class="player-play-btn" onclick="toggleChatPlayerPlay('${lessonId}')">⏸ Pause</button>
                <button onclick="seekChatPlayer('${lessonId}', -10)">⏪ Seek -10s</button>
                <button onclick="seekChatPlayer('${lessonId}', 10)">Seek +10s ⏩</button>
            </div>
            <div class="custom-player-right-group">
                <select onchange="changeChatPlayerSpeed('${lessonId}', this.value)" style="color: #fff; background: rgba(0,0,0,0.6); padding: 4px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.2);">
                    <option value="0.5">0.5x</option>
                    <option value="1.0" selected>1.0x</option>
                    <option value="1.25">1.25x</option>
                    <option value="1.5">1.5x</option>
                    <option value="2.0">2.0x</option>
                </select>
                <button onclick="toggleChatPlayerFullscreen('${lessonId}')">⛶ Fullscreen</button>
            </div>
        </div>
    `;

    const contentDiv = cardElement.querySelector('.markdown-content');
    if (contentDiv) {
        contentDiv.after(playerDiv);
    } else {
        cardElement.appendChild(playerDiv);
    }
};

window.toggleChatPlayerPlay = function(lessonId) {
    const iframe = document.getElementById(`vl-chat-iframe-${lessonId}`);
    if (!iframe || !iframe.contentWindow) return;
    const btn = iframe.parentNode.querySelector('.player-play-btn');
    const playerState = window.chatPlayers[lessonId];
    if (playerState.isPlaying) {
        iframe.contentWindow.postMessage({ target: 'HYPERFRAMES_ENGINE', command: 'PAUSE' }, '*');
        btn.innerHTML = '▶ Play';
        playerState.isPlaying = false;
    } else {
        iframe.contentWindow.postMessage({ target: 'HYPERFRAMES_ENGINE', command: 'PLAY' }, '*');
        btn.innerHTML = '⏸ Pause';
        playerState.isPlaying = true;
    }
};

window.seekChatPlayer = function(lessonId, offset) {
    const iframe = document.getElementById(`vl-chat-iframe-${lessonId}`);
    if (!iframe || !iframe.contentWindow) return;
    const playerState = window.chatPlayers[lessonId];
    const target = Math.max(0, Math.min(playerState.duration || 300, (playerState.currentTime || 0) + offset));
    iframe.contentWindow.postMessage({ target: 'HYPERFRAMES_ENGINE', command: 'SEEK', targetTime: target }, '*');
};

window.changeChatPlayerSpeed = function(lessonId, speed) {
    const iframe = document.getElementById(`vl-chat-iframe-${lessonId}`);
    if (!iframe || !iframe.contentWindow) return;
    iframe.contentWindow.postMessage({ target: 'HYPERFRAMES_ENGINE', command: 'SET_PLAYBACK_RATE', rate: parseFloat(speed) }, '*');
};

window.toggleChatPlayerFullscreen = function(lessonId) {
    const iframe = document.getElementById(`vl-chat-iframe-${lessonId}`);
    if (!iframe) return;
    const container = iframe.parentNode;
    if (!document.fullscreenElement) {
        if (container.requestFullscreen) {
            container.requestFullscreen();
        }
    } else {
        if (document.exitFullscreen) {
            document.exitFullscreen();
        }
    }
};

// Global cross-document listener to receive time updates and playback state from hyperframes engines
window.addEventListener('message', (e) => {
    const data = e.data;
    if (!data || data.source !== 'HYPERFRAMES_ENGINE') return;

    const iframes = document.querySelectorAll('.custom-player-iframe');
    let lessonId = null;
    let targetIframe = null;
    iframes.forEach(iframe => {
        if (iframe.contentWindow === e.source) {
            targetIframe = iframe;
            lessonId = iframe.id.replace('vl-chat-iframe-', '');
        }
    });

    if (!lessonId || !window.chatPlayers[lessonId]) return;

    if (data.type === 'CURRENT_TIME') {
        window.chatPlayers[lessonId].currentTime = data.currentTime;
        window.chatPlayers[lessonId].duration = data.duration;
    } else if (data.type === 'PLAYING') {
        window.chatPlayers[lessonId].isPlaying = true;
        const btn = targetIframe.parentNode.querySelector('.player-play-btn');
        if (btn) btn.innerHTML = '⏸ Pause';
    }
});

// Immediately register setupChatSubmitGlobal on script load (top-level scope)
try {
    setupChatSubmitGlobal();
    console.log('[script.js] setupChatSubmitGlobal executed at script load time.');
} catch (e) {
    console.warn('[script.js] Initial setupChatSubmitGlobal error:', e);
}
