import { Link } from "@tanstack/react-router";
import { Compass } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { Button } from "@/components/ui/button";

export function NotFound() {
  return (
    <div className="py-10">
      <h1 className="sr-only">Page not found</h1>
      <EmptyState
        icon={Compass}
        title="This page does not exist"
        description="The link may be from an older version of Synthetic Platform. Every view is reachable from the tabs above."
        action={
          <Button asChild variant="primary">
            <Link to="/">Go to Intro</Link>
          </Button>
        }
      />
    </div>
  );
}
