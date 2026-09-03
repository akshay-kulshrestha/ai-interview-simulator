(() => {
    const data = JSON.parse(document.getElementById("session-data").textContent);
    const sessionId = data.session_id;
    const totalQuestions = data.total_questions;
    const personality = data.personality;
    let currentQuestion = data.question;

    const CATEGORY_META = {
        technical: { label: "Technical", icon: "code-2", badgeClass: "badge-blue" },
        behavioral: { label: "Behavioral", icon: "users", badgeClass: "badge-emerald" },
        "problem-solving": { label: "Problem Solving", icon: "puzzle", badgeClass: "badge-amber" },
        hr: { label: "Behavioral", icon: "users", badgeClass: "badge-emerald" },
    };

    const PERSONALITY_ICON = { friendly: "smile", professional: "briefcase", strict: "target" };

    const ANALYZING_STEPS = [
        { label: "Checking keyword coverage", icon: "target" },
        { label: "Assessing answer structure", icon: "gauge" },
        { label: "Evaluating clarity & depth", icon: "zap" },
        { label: "Generating feedback", icon: "sparkles" },
    ];

    // ---------------------------------------------------------- markdown --
    // The backend (ai_analyzer.py) explicitly formats improved_answer as
    // markdown (paragraphs separated by blank lines, "- " bullet lists,
    // fenced ```code``` blocks) -- this renders that into safe HTML instead
    // of dumping it as one unstyled block of text with literal "**"/"`"
    // characters showing. Escapes HTML first (this text originates from an
    // LLM, so it's treated as untrusted) then applies a small set of
    // markdown patterns on top of the escaped text.
    function escapeHtml(str) {
        return str
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;");
    }

    function inlineMarkdown(str) {
        return str
            .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
            .replace(/`([^`]+)`/g, "<code>$1</code>");
    }

    function renderMarkdown(text) {
        const escaped = escapeHtml(text);
        const blocks = escaped.split(/\n\s*\n/);
        return blocks.map(block => {
            const trimmed = block.trim();
            if (!trimmed) return "";

            const codeMatch = trimmed.match(/^```(\w*)\n([\s\S]*?)\n?```$/);
            if (codeMatch) {
                return `<pre><code>${codeMatch[2]}</code></pre>`;
            }

            const lines = trimmed.split("\n").map(l => l.trim()).filter(Boolean);
            const isList = lines.length > 0 && lines.every(l => /^[-*]\s+/.test(l));
            if (isList) {
                const items = lines.map(l => `<li>${inlineMarkdown(l.replace(/^[-*]\s+/, ""))}</li>`).join("");
                return `<ul>${items}</ul>`;
            }

            return `<p>${inlineMarkdown(trimmed.replace(/\n/g, "<br>"))}</p>`;
        }).join("");
    }

    const els = {
        segmentedProgress: document.getElementById("segmented-progress"),
        progressPct: document.getElementById("progress-pct"),
        personaBadge: document.getElementById("persona-badge"),
        counter: document.getElementById("question-counter"),
        greetingCard: document.getElementById("greeting-card"),
        questionView: document.getElementById("question-view"),
        categoryBadge: document.getElementById("category-badge"),
        questionText: document.getElementById("question-text"),
        timerText: document.getElementById("timer-text"),
        timerWrap: document.getElementById("timer"),
        hintsToggle: document.getElementById("hints-toggle"),
        hintsToggleText: document.getElementById("hints-toggle-text"),
        hintsChevron: document.getElementById("hints-chevron"),
        hintsList: document.getElementById("hints-list"),
        answerBox: document.getElementById("answer-box"),
        qualityLabel: document.getElementById("quality-label"),
        qualityFill: document.getElementById("quality-fill"),
        micError: document.getElementById("mic-error"),
        micErrorText: document.getElementById("mic-error-text"),
        micBtn: document.getElementById("mic-btn"),
        micIconOn: document.getElementById("mic-icon-on"),
        micIconOff: document.getElementById("mic-icon-off"),
        micLabel: document.getElementById("mic-label"),
        wordCount: document.getElementById("word-count"),
        submitBtn: document.getElementById("submit-btn"),
        errorBox: document.getElementById("error"),
        analyzingView: document.getElementById("analyzing-view"),
        analyzingSteps: document.getElementById("analyzing-steps"),
        improvedToggle: document.getElementById("improved-toggle"),
        improvedBody: document.getElementById("improved-body"),
        feedbackView: document.getElementById("feedback-view"),
        recapBadge: document.getElementById("recap-badge"),
        recapQuestion: document.getElementById("recap-question"),
        recapAnswer: document.getElementById("recap-answer"),
        feedbackScoreRing: document.getElementById("feedback-score-ring"),
        verdictTitle: document.getElementById("verdict-title"),
        verdictSub: document.getElementById("verdict-sub"),
        fillerNote: document.getElementById("filler-note"),
        strengthsBlock: document.getElementById("strengths-block"),
        strengthsList: document.getElementById("strengths-list"),
        suggestionsBlock: document.getElementById("suggestions-block"),
        suggestionsList: document.getElementById("suggestions-list"),
        improvedBlock: document.getElementById("improved-block"),
        improvedAnswer: document.getElementById("improved-answer"),
        nextBtn: document.getElementById("next-btn"),
    };

    // Preserve the line breaks/spacing already present in the candidate's
    // own typed answer (recap-answer uses textContent, which is safe from
    // injection, but the browser still collapses whitespace visually unless
    // told not to).
    if (els.recapAnswer) {
        els.recapAnswer.style.whiteSpace = "pre-wrap";
    }

    function refreshIcons() {
        if (window.lucide) lucide.createIcons();
    }

    // -------------------------------------------------------------- persona
    if (els.personaBadge && personality) {
        const icon = PERSONALITY_ICON[personality] || "user";
        els.personaBadge.innerHTML = `<i data-lucide="${icon}" class="icon"></i><span>${personality}</span>`;
    }

    // -------------------------------------------------------------- progress
    for (let i = 0; i < totalQuestions; i++) {
        const seg = document.createElement("div");
        seg.className = "progress-segment";
        els.segmentedProgress.appendChild(seg);
    }
    const progressSegments = [...els.segmentedProgress.children];

    function renderProgress(index, isFeedback) {
        progressSegments.forEach((seg, i) => {
            const done = i < index || (i === index && isFeedback);
            seg.className = `progress-segment ${done ? "done" : i === index ? "current" : ""}`;
        });
        const pct = Math.round(((index + (isFeedback ? 1 : 0)) / totalQuestions) * 100);
        els.progressPct.textContent = `${pct}% complete`;
    }

    // ---------------------------------------------------------------- timer
    const TIME_SOFT_LIMIT = 120;
    let elapsedSeconds = 0;
    let timerHandle = null;

    function formatTime(total) {
        const m = String(Math.floor(total / 60)).padStart(2, "0");
        const s = String(total % 60).padStart(2, "0");
        return `${m}:${s}`;
    }

    function startTimer() {
        stopTimer();
        elapsedSeconds = 0;
        els.timerText.textContent = formatTime(0);
        els.timerWrap.classList.remove("over");
        timerHandle = setInterval(() => {
            elapsedSeconds += 1;
            els.timerText.textContent = formatTime(elapsedSeconds);
            els.timerWrap.classList.toggle("over", elapsedSeconds > TIME_SOFT_LIMIT);
        }, 1000);
    }

    function stopTimer() {
        if (timerHandle) clearInterval(timerHandle);
        timerHandle = null;
    }

    // ------------------------------------------------------------ score ring
    function buildScoreRing(container, score, maxScore, size, stroke, label) {
        const r = (size - stroke) / 2;
        const c = 2 * Math.PI * r;
        const ratio = Math.max(0, Math.min(1, score / maxScore));
        const offset = c * (1 - ratio);
        const pct = ratio * 100;
        const color = pct >= 80 ? "#16a34a" : pct >= 60 ? "#2563eb" : pct >= 40 ? "#f59e0b" : "#dc2626";
        container.style.width = `${size}px`;
        container.style.height = `${size}px`;
        container.innerHTML = `
            <svg width="${size}" height="${size}">
                <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--ring-track)" stroke-width="${stroke}"></circle>
                <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}"
                    stroke-dasharray="${c}" stroke-dashoffset="${offset}" stroke-linecap="round"
                    style="transition: stroke-dashoffset .8s ease-out"></circle>
            </svg>
            <div class="center">
                <span class="val" style="color:${color}; font-size:${size * 0.22}px;">${score.toFixed(1)}</span>
                <span class="max">/ ${maxScore}</span>
                ${label ? `<span class="lbl">${label}</span>` : ""}
            </div>`;
        return color;
    }

    // ------------------------------------------------------------ rendering
    function renderCategoryBadge(el, category) {
        const meta = CATEGORY_META[category] || CATEGORY_META.technical;
        el.className = `badge ${meta.badgeClass}`;
        el.innerHTML = `<i data-lucide="${meta.icon}" class="icon" style="width:13px;height:13px;"></i>${meta.label}`;
    }

    function renderQuestion(question) {
        currentQuestion = question;
        renderCategoryBadge(els.categoryBadge, question.category);
        els.questionText.textContent = question.question_text;
        els.counter.textContent = `Question ${question.order_index + 1} of ${totalQuestions}`;
        renderProgress(question.order_index, false);

        els.hintsList.innerHTML = "";
        els.hintsList.classList.add("hidden");
        els.hintsToggleText.textContent = "Need a hint?";
        els.hintsToggle.disabled = false;
        els.hintsChevron.classList.remove("open");

        if (els.greetingCard) {
            els.greetingCard.classList.toggle("hidden", question.order_index !== 0);
        }

        els.answerBox.value = "";
        updateWordCount();
        els.micError.classList.add("hidden");
        els.errorBox.classList.add("hidden");
        els.submitBtn.disabled = false;
        els.submitBtn.innerHTML = '<i data-lucide="send" class="icon" style="width:15px;height:15px;"></i>Submit Answer';

        els.improvedToggle.setAttribute("aria-expanded", "false");
        els.improvedBody.classList.add("hidden");

        els.feedbackView.classList.add("hidden");
        els.analyzingView.classList.add("hidden");
        els.questionView.classList.remove("hidden");

        startTimer();
        refreshIcons();
    }

    function updateWordCount() {
        const text = els.answerBox.value.trim();
        const words = text ? text.split(/\s+/).filter(Boolean).length : 0;
        els.wordCount.textContent = words;

        const level = words >= 50 ? "high" : words >= 20 ? "medium" : words >= 5 ? "low" : "none";
        const labels = { none: "Start typing...", low: "Building...", medium: "Good detail", high: "Great detail" };
        els.qualityLabel.textContent = labels[level];
        els.qualityFill.className = `quality-fill ${level}`;
        els.qualityFill.style.width = `${Math.min(100, (words / 60) * 100)}%`;
    }
    els.answerBox.addEventListener("input", updateWordCount);

    els.improvedToggle.addEventListener("click", () => {
        const expanded = els.improvedToggle.getAttribute("aria-expanded") === "true";
        els.improvedToggle.setAttribute("aria-expanded", String(!expanded));
        els.improvedBody.classList.toggle("hidden", expanded);
    });

    function verdictMessage(score) {
        if (score >= 8) return "Excellent answer! Well structured and thorough.";
        if (score >= 6) return "Good answer with room for improvement.";
        if (score >= 4) return "Decent attempt, but needs more depth.";
        return "This answer needs significant improvement.";
    }

    function fillList(block, list, items) {
        list.innerHTML = "";
        if (!items || items.length === 0) {
            block.classList.add("hidden");
            return;
        }
        items.forEach(item => {
            const li = document.createElement("li");
            li.innerHTML = `<span class="dot"></span><span></span>`;
            li.querySelector("span:last-child").textContent = item;
            list.appendChild(li);
        });
        block.classList.remove("hidden");
    }

    function renderFeedback(question, answerText, feedback, sessionComplete) {
        renderProgress(question.order_index, true);
        renderCategoryBadge(els.recapBadge, question.category);
        els.recapQuestion.textContent = question.question_text;
        els.recapAnswer.textContent = answerText;

        const color = buildScoreRing(els.feedbackScoreRing, feedback.score, 10, 110, 9, "Score");
        els.verdictTitle.textContent = "AI Analysis Complete";
        els.verdictTitle.style.color = "";
        els.verdictSub.textContent = verdictMessage(feedback.score);
        els.verdictSub.style.color = color;

        if (feedback.filler_word_count > 0) {
            const words = feedback.filler_words.map(f => `"${f.phrase}" ×${f.count}`).join(", ");
            els.fillerNote.innerHTML = `<i data-lucide="info" class="icon" style="width:14px;height:14px;"></i><span>Filler words detected: ${words}</span>`;
            els.fillerNote.classList.remove("hidden");
        } else {
            els.fillerNote.classList.add("hidden");
        }

        fillList(els.strengthsBlock, els.strengthsList, feedback.strengths);
        fillList(els.suggestionsBlock, els.suggestionsList, feedback.suggestions);

        if (feedback.improved_answer) {
            els.improvedAnswer.innerHTML = renderMarkdown(feedback.improved_answer);
            els.improvedBlock.classList.remove("hidden");
        } else {
            els.improvedBlock.classList.add("hidden");
        }

        els.nextBtn.disabled = false;
        els.nextBtn.innerHTML = sessionComplete
            ? '<i data-lucide="award" class="icon" style="width:16px;height:16px;"></i>Finish &amp; See Report'
            : '<i data-lucide="arrow-right" class="icon" style="width:16px;height:16px;"></i>Next Question';

        els.questionView.classList.add("hidden");
        els.analyzingView.classList.add("hidden");
        els.feedbackView.classList.remove("hidden");
        refreshIcons();
        els.feedbackView.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    // ---------------------------------------------------------- analyzing
    let analyzingInterval = null;
    let analyzingStepIndex = 0;

    function updateAnalyzingSteps() {
        [...els.analyzingSteps.children].forEach((el, i) => {
            el.className = `analyzing-step ${i < analyzingStepIndex ? "done" : i === analyzingStepIndex ? "current" : ""}`;
        });
    }

    function startAnalyzingAnimation() {
        els.analyzingSteps.innerHTML = ANALYZING_STEPS.map(s =>
            `<div class="analyzing-step"><i data-lucide="${s.icon}" class="icon"></i><span>${s.label}</span></div>`
        ).join("");
        refreshIcons();
        analyzingStepIndex = 0;
        updateAnalyzingSteps();
        analyzingInterval = setInterval(() => {
            analyzingStepIndex = (analyzingStepIndex + 1) % ANALYZING_STEPS.length;
            updateAnalyzingSteps();
        }, 1800);
    }

    function stopAnalyzingAnimation() {
        if (analyzingInterval) clearInterval(analyzingInterval);
        analyzingInterval = null;
    }

    // ------------------------------------------------------------- submit
    let pendingResult = null;
    let nextQuestionPromise = null;

    async function fetchNextQuestion() {
        const res = await fetch(`/api/session/${sessionId}/next-question`, { method: "POST" });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "Couldn't load the next question.");
        return data.question;
    }

    async function submitAnswer() {
        const answer = els.answerBox.value.trim();
        if (answer.length < 3) {
            els.errorBox.textContent = "Please enter an answer before submitting.";
            els.errorBox.classList.remove("hidden");
            return;
        }
        stopTimer();
        els.errorBox.classList.add("hidden");
        if (els.greetingCard) els.greetingCard.classList.add("hidden");

        els.questionView.classList.add("hidden");
        els.analyzingView.classList.remove("hidden");
        startAnalyzingAnimation();
        refreshIcons();

        try {
            const res = await fetch(`/api/session/${sessionId}/answer`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ answer, time_taken_seconds: elapsedSeconds }),
            });
            const result = await res.json();
            if (!res.ok) throw new Error(result.error || "Something went wrong.");

            pendingResult = result;
            if (!result.session_complete) {
                // Kick off next-question generation now, in the background, so it's
                // likely ready by the time the user finishes reading their feedback.
                nextQuestionPromise = fetchNextQuestion();
                nextQuestionPromise.catch(() => {});
            }
            stopAnalyzingAnimation();
            renderFeedback(result.question, answer, result.feedback, result.session_complete);
        } catch (err) {
            stopAnalyzingAnimation();
            els.analyzingView.classList.add("hidden");
            els.questionView.classList.remove("hidden");
            els.errorBox.textContent = err.message;
            els.errorBox.classList.remove("hidden");
            startTimer();
        }
    }

    els.submitBtn.addEventListener("click", submitAnswer);

    els.answerBox.addEventListener("keydown", (e) => {
        if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            submitAnswer();
        }
    });

    els.nextBtn.addEventListener("click", async () => {
        if (!pendingResult) return;
        if (pendingResult.session_complete) {
            window.location.href = `/report/${sessionId}`;
            return;
        }

        const originalHTML = els.nextBtn.innerHTML;
        els.nextBtn.disabled = true;
        els.nextBtn.innerHTML = '<i data-lucide="loader-circle" class="icon" style="width:16px;height:16px;animation:spin 1s linear infinite;"></i>Loading next question...';
        refreshIcons();

        // If the background fetch never started, or a previous attempt already
        // failed (leaving a settled, rejected promise behind), start a fresh one
        // rather than re-awaiting a promise that can only ever reject again.
        if (!nextQuestionPromise) {
            nextQuestionPromise = fetchNextQuestion();
        }

        try {
            const question = await nextQuestionPromise;
            renderQuestion(question);
            pendingResult = null;
            nextQuestionPromise = null;
        } catch (err) {
            nextQuestionPromise = null;
            els.nextBtn.disabled = false;
            els.nextBtn.innerHTML = originalHTML;
            refreshIcons();
            els.errorBox.textContent = err.message;
            els.errorBox.classList.remove("hidden");
        }
    });

    // ------------------------------------------------------------- hints
    function renderHintsList(hints) {
        els.hintsList.innerHTML = "";
        const items = hints && hints.length ? hints : ["No hints available for this question."];
        items.forEach(h => {
            const li = document.createElement("li");
            li.innerHTML = `<i data-lucide="lightbulb" class="icon"></i><span></span>`;
            li.querySelector("span").textContent = h;
            els.hintsList.appendChild(li);
        });
        refreshIcons();
    }

    els.hintsToggle.addEventListener("click", async () => {
        const willShow = els.hintsList.classList.contains("hidden");

        if (willShow && !currentQuestion.hintsLoaded) {
            els.hintsToggle.disabled = true;
            els.hintsToggleText.textContent = "Loading hints...";
            try {
                const res = await fetch(`/api/session/${sessionId}/question/${currentQuestion.id}/hints`);
                const data = await res.json();
                if (!res.ok) throw new Error(data.error || "Couldn't load hints.");
                currentQuestion.hints = data.hints || [];
            } catch (err) {
                currentQuestion.hints = [];
            }
            currentQuestion.hintsLoaded = true;
            els.hintsToggle.disabled = false;
            renderHintsList(currentQuestion.hints);
        }

        els.hintsList.classList.toggle("hidden", !willShow);
        els.hintsToggleText.textContent = willShow ? "Hide hints" : "Need a hint?";
        els.hintsChevron.classList.toggle("open", willShow);
    });

    // ------------------------------------------------------- speech-to-text
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (SpeechRecognition) {
        const recognizer = new SpeechRecognition();
        recognizer.continuous = true;
        recognizer.interimResults = true;
        recognizer.lang = "en-US";

        let recording = false;
        let baseText = "";

        function setRecording(state) {
            recording = state;
            els.micBtn.classList.toggle("recording", state);
            els.micIconOn.classList.toggle("hidden", state);
            els.micIconOff.classList.toggle("hidden", !state);
            els.micLabel.textContent = state ? "Stop" : "Speak";
        }

        recognizer.addEventListener("result", (event) => {
            let transcript = "";
            for (let i = 0; i < event.results.length; i++) {
                transcript += event.results[i][0].transcript;
            }
            els.answerBox.value = (baseText + " " + transcript).trim();
            updateWordCount();
        });

        recognizer.addEventListener("end", () => setRecording(false));

        recognizer.addEventListener("error", (event) => {
            let message = "Voice input error. Please try again.";
            if (event.error === "not-allowed" || event.error === "permission-denied") {
                message = "Microphone access denied. Please allow microphone permissions.";
            }
            els.micErrorText.textContent = message;
            els.micError.classList.remove("hidden");
        });

        els.micBtn.addEventListener("click", () => {
            if (recording) {
                recognizer.stop();
                return;
            }
            els.micError.classList.add("hidden");
            baseText = els.answerBox.value;
            recognizer.start();
            setRecording(true);
        });
    } else {
        els.micBtn.disabled = true;
        els.micErrorText.textContent = "Voice input is not supported in this browser. Try Chrome or Edge.";
        els.micError.classList.remove("hidden");
    }

    // --------------------------------------------------------------- init
    renderQuestion(currentQuestion);
})();
