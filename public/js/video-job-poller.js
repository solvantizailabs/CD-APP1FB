/**
 * Part I - async MP4 video job creation + status polling.
 *
 * Deliberately a standalone module, not wired into conversation.js's existing
 * chat flow here - that flow (public/js/conversation.js) already streams
 * narrated interactive lessons live via SSE and works today; wiring a real,
 * separate MP4 request into it is a UI/UX decision (where does the "Watch
 * Video" button live, does it replace or sit alongside the interactive
 * lesson) that needs a product call, not a guess made while racing a
 * deadline. This module is the working building block for whichever screen
 * ends up calling it.
 */

async function requestVideoJob({ query, className, subject, bookUuid, studentId, sessionId, questionId }) {
  const res = await fetch("/api/video/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      class_name: className,
      subject: subject || "General Knowledge",
      book_uuid: bookUuid || "",
      student_id: studentId || "",
      session_id: sessionId || "",
      question_id: questionId || "",
    }),
  });
  if (!res.ok) {
    throw new Error(`Video job creation failed: ${res.status}`);
  }
  return res.json(); // { video_job_id, video_status }
}

async function pollVideoStatus(jobId, { intervalMs = 4000, onUpdate } = {}) {
  return new Promise((resolve, reject) => {
    const poll = async () => {
      try {
        const res = await fetch(`/api/video/${jobId}/status`);
        if (!res.ok) throw new Error(`Status check failed: ${res.status}`);
        const data = await res.json();
        if (onUpdate) onUpdate(data);

        if (data.status === "completed") {
          resolve(data);
        } else if (data.status === "failed") {
          reject(new Error(data.error_message || "Video generation failed"));
        } else {
          setTimeout(poll, intervalMs);
        }
      } catch (err) {
        reject(err);
      }
    };
    poll();
  });
}

// Example usage (Part I's flow: display answer -> "Generating video..." ->
// poll -> completed -> show Watch Video):
//
// const { video_job_id } = await requestVideoJob({ query, className: "10", subject: "Science" });
// showGeneratingVideoIndicator();
// try {
//   const finalStatus = await pollVideoStatus(video_job_id, {
//     onUpdate: (s) => console.log("video status:", s.status),
//   });
//   showWatchVideoButton(finalStatus.video_url);
// } catch (err) {
//   showVideoFailedMessage(err.message);
// }

window.DronaXVideo = { requestVideoJob, pollVideoStatus };
