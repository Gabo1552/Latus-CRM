import * as React from "react"

import { cn } from "@/lib/utils"

const Textarea = React.forwardRef(({ className, onChange, ...props }, ref) => {
  const localRef = React.useRef(null);
  
  React.useImperativeHandle(ref, () => localRef.current);

  const adjustHeight = React.useCallback(() => {
    const textarea = localRef.current;
    if (textarea) {
      textarea.style.height = "auto";
      const style = window.getComputedStyle(textarea);
      const borderTop = parseFloat(style.borderTopWidth) || 0;
      const borderBottom = parseFloat(style.borderBottomWidth) || 0;
      textarea.style.height = `${textarea.scrollHeight + borderTop + borderBottom}px`;
    }
  }, []);

  React.useEffect(() => {
    adjustHeight();
  }, [props.value, adjustHeight]);

  const handleChange = (e) => {
    adjustHeight();
    if (onChange) {
      onChange(e);
    }
  };

  return (
    <textarea
      className={cn(
        "flex min-h-[60px] w-full rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50 md:text-sm resize-none overflow-y-hidden",
        className
      )}
      ref={localRef}
      onChange={handleChange}
      {...props}
    />
  );
})
Textarea.displayName = "Textarea"

export { Textarea }
