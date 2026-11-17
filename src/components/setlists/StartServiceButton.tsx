/**
 * The one way to start a service. The big button picks the setup from the
 * displays connected — a projector as a second screen gets the operator view
 * here and the slides there; a lone screen presents full-screen. The arrow
 * on its edge offers both explicitly, for when the guess is wrong.
 */

import { useEffect, useState } from "react";
import { ChevronDown, MonitorPlay, Play, Presentation } from "lucide-react";

import type { StartHow } from "@/components/AppShell";
import { Button } from "@/components/ui/button";
import { Menu, MenuItem } from "@/components/ui/Menu";
import { monitorCount } from "@/lib/presentation/projection";
import { cn } from "@/lib/utils";

export function StartServiceButton({
  onStart,
  disabled,
  className,
}: {
  onStart: (how: StartHow) => void;
  disabled?: boolean;
  className?: string;
}) {
  const [screens, setScreens] = useState<number | null>(null);
  useEffect(() => {
    void monitorCount().then(setScreens);
  }, []);

  const autoHint =
    screens === null
      ? "Start the service"
      : screens > 1
        ? "Controls on this screen, slides on the projector"
        : "Present full-screen on this screen";

  return (
    <div className={cn("flex items-center", className)}>
      <Button
        size="sm"
        onClick={() => onStart("auto")}
        disabled={disabled}
        title={autoHint}
        className="rounded-r-none"
        data-testid="start-service"
      >
        <Play className="size-4" />
        Start service
      </Button>
      <Menu
        align="right"
        panelClassName="w-80"
        trigger={(toggle, open) => (
          <Button
            size="sm"
            onClick={toggle}
            disabled={disabled}
            aria-label="Other ways to start"
            aria-haspopup="menu"
            aria-expanded={open}
            title="Other ways to start"
            className="rounded-l-none border-l border-primary-foreground/20 px-2"
          >
            <ChevronDown className="size-4" />
          </Button>
        )}
      >
        <MenuItem
          icon={<MonitorPlay />}
          onSelect={() => onStart("operator")}
          hint="Slides on a projector or second screen; every slide and song to click here"
        >
          Operator view + audience screen
          {screens !== null && screens > 1 && <AutoTag />}
        </MenuItem>
        <MenuItem
          icon={<Presentation />}
          onSelect={() => onStart("single")}
          hint="This screen shows the slides; move the mouse to the bottom for controls"
        >
          Present on this screen only
          {screens === 1 && <AutoTag />}
        </MenuItem>
      </Menu>
    </div>
  );
}

/** Marks the option "Start service" picks on this computer right now. */
function AutoTag() {
  return (
    <span className="ml-2 rounded bg-accent px-1.5 py-0.5 text-[10px] text-muted-foreground">
      Start service does this
    </span>
  );
}
