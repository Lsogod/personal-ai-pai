import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap rounded-xl text-sm font-medium transition-all duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 focus-visible:ring-offset-2 focus-visible:ring-offset-surface disabled:pointer-events-none disabled:opacity-40 select-none",
  {
    variants: {
      variant: {
        default: "bg-content text-surface hover:bg-accent-hover shadow-subtle active:scale-[0.97]",
        ghost: "border border-border bg-surface-card text-content hover:bg-surface-hover hover:border-border-hover",
        subtle: "bg-surface-hover text-content-secondary hover:bg-surface-active",
        danger: "bg-danger/10 text-danger hover:bg-danger/20",
        icon: "text-content-secondary hover:text-content hover:bg-surface-hover rounded-lg",
      },
      size: {
        default: "h-10 px-4 gap-2",
        sm: "h-8 px-3 text-xs gap-1.5",
        lg: "h-11 px-5 gap-2",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button
      ref={ref}
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  )
);
Button.displayName = "Button";
