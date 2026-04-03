import React, { useState } from "react";

export default function ConfirmActionButton({
  children,
  confirmText,
  onConfirm,
  disabled = false,
  className = "",
  confirmWithDialog = true,
}) {
  const [pending, setPending] = useState(false);

  const handleClick = async () => {
    if (disabled || pending) {
      return;
    }
    if (confirmWithDialog) {
      const approved = window.confirm(confirmText);
      if (!approved) {
        return;
      }
    }
    setPending(true);
    try {
      await onConfirm();
    } finally {
      setPending(false);
    }
  };

  return (
    <button type="button" className={className} onClick={handleClick} disabled={disabled || pending}>
      {pending ? "Working..." : children}
    </button>
  );
}
