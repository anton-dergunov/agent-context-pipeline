;;; info-triage.el --- Review the info-triage inbox from Emacs  -*- lexical-binding: t; -*-

;; Three commands, deliberately.  Emacs owns `triage.org' — the queue you scan
;; and select from — and VS Code owns the artifacts, because it already renders
;; PDF, video and Markdown preview with no configuration.  Turning Emacs into a
;; file browser for the item directories would be a lot of work to arrive
;; somewhere worse.
;;
;; Load it from your init with:
;;
;;   (load "~/projects/tools/info-triage/emacs/info-triage.el")
;;
;; No package dependencies; `org' is only needed for `info-triage-back'.

;;; Code:

(require 'org)

(defgroup info-triage nil
  "Review the info-triage inbox."
  :group 'external)

(defcustom info-triage-inbox-directory (expand-file-name "~/info-triage-inbox/")
  "Directory `sync.sh' synchronizes the NAS inbox into."
  :type 'directory
  :group 'info-triage)

(defcustom info-triage-sync-script
  (expand-file-name "~/projects/tools/info-triage/sync.sh")
  "Path to the synchronization script."
  :type 'file
  :group 'info-triage)

(defcustom info-triage-editor-command "code"
  "External editor used to inspect an item's directory."
  :type 'string
  :group 'info-triage)

(defun info-triage--org-file ()
  (expand-file-name "triage.org" info-triage-inbox-directory))

;;;###autoload
(defun info-triage-inbox ()
  "Open the generated inbox overview."
  (interactive)
  (let ((file (info-triage--org-file)))
    (unless (file-exists-p file)
      (user-error "No %s yet — run `info-triage-sync' first" file))
    (find-file file)))

;;;###autoload
(defun info-triage-sync ()
  "Synchronize the NAS inbox, then refresh the overview if it is open."
  (interactive)
  (let ((buffer (get-file-buffer (info-triage--org-file))))
    (compilation-start (shell-quote-argument info-triage-sync-script)
                       nil
                       (lambda (&rest _) "*info-triage sync*"))
    ;; The file is regenerated wholesale and carries no state, so reverting it
    ;; can never lose a mark: deleting an item's directory is the only signal.
    (when buffer
      (with-current-buffer buffer
        (revert-buffer :ignore-auto :noconfirm)))))

(defun info-triage--item-directory ()
  "Directory of the item at point, or the inbox itself outside any item."
  (let ((dir (and (derived-mode-p 'org-mode)
                  (org-entry-get (point) "DIR"))))
    (expand-file-name (or dir "") info-triage-inbox-directory)))

;;;###autoload
(defun info-triage-open-externally (&optional whole-inbox)
  "Open the item at point in `info-triage-editor-command'.
With a prefix argument, or outside an item, open WHOLE-INBOX instead."
  (interactive "P")
  (let ((target (if whole-inbox
                    (expand-file-name info-triage-inbox-directory)
                  (info-triage--item-directory))))
    (start-process "info-triage-editor" nil info-triage-editor-command target)
    (message "Opened %s" target)))

;;;###autoload
(defun info-triage-back ()
  "Return from a link followed out of the overview."
  (interactive)
  (org-mark-ring-goto))

(provide 'info-triage)

;;; info-triage.el ends here
