import { useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import {
  Dropdown,
  DropdownItem,
  DropdownList,
  Label,
  Masthead,
  MastheadBrand,
  MastheadContent,
  MastheadMain,
  MastheadToggle,
  MenuToggle,
  Nav,
  NavExpandable,
  NavItem,
  NavList,
  Page,
  PageSidebar,
  PageSidebarBody,
  PageToggleButton,
  SkipToContent,
  Title,
  Toolbar,
  ToolbarContent,
  ToolbarGroup,
  ToolbarItem,
} from "@patternfly/react-core";
import BarsIcon from "@patternfly/react-icons/dist/esm/icons/bars-icon";
import UserIcon from "@patternfly/react-icons/dist/esm/icons/user-icon";
import { useAuth, useUser } from "../auth";
import { useToast } from "../toast";

export const ADMIN_LINKS = [
  { to: "/admin/users", label: "Users" },
  { to: "/admin/agents", label: "Agents" },
  { to: "/admin/credentials", label: "Credentials" },
  { to: "/admin/repositories", label: "Repositories" },
  { to: "/admin/git-hosts", label: "Git hosts" },
  { to: "/admin/notifications", label: "Notifications" },
  { to: "/admin/settings", label: "Settings" },
  { to: "/admin/api-tokens", label: "API tokens" },
  { to: "/admin/audit", label: "Audit log" },
];

const ROLE_COLOR = { admin: "purple", member: "blue", viewer: "grey" } as const;

export function AppLayout() {
  const user = useUser();
  const { logout } = useAuth();
  const toast = useToast();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const isAdmin = user.role === "admin";

  const isActive = (to: string) =>
    to === "/" ? pathname === "/" || pathname.startsWith("/reviews") : pathname.startsWith(to);

  const doLogout = async () => {
    try {
      await logout();
    } catch {
      toast.warning("Logout request failed; local session cleared");
    }
    navigate("/login");
  };

  const header = (
    <Masthead>
      <MastheadToggle>
        <PageToggleButton variant="plain" aria-label="Toggle navigation">
          <BarsIcon />
        </PageToggleButton>
      </MastheadToggle>
      <MastheadMain>
        <MastheadBrand component={(props) => <Link {...props} to="/" />}>
          <Title headingLevel="h1" size="xl" style={{ color: "white" }}>
            Eagle
          </Title>
        </MastheadBrand>
      </MastheadMain>
      <MastheadContent>
        <Toolbar isFullHeight isStatic>
          <ToolbarContent>
            <ToolbarGroup align={{ default: "alignRight" }}>
              <ToolbarItem>
                <Dropdown
                  isOpen={menuOpen}
                  onOpenChange={setMenuOpen}
                  onSelect={() => setMenuOpen(false)}
                  popperProps={{ position: "right" }}
                  toggle={(ref) => (
                    <MenuToggle
                      ref={ref}
                      variant="plainText"
                      icon={<UserIcon />}
                      isExpanded={menuOpen}
                      onClick={() => setMenuOpen((o) => !o)}
                      aria-label="User menu"
                    >
                      {user.username}{" "}
                      <Label isCompact color={ROLE_COLOR[user.role]}>
                        {user.role}
                      </Label>
                    </MenuToggle>
                  )}
                >
                  <DropdownList>
                    <DropdownItem key="account" onClick={() => navigate("/account")}>
                      Account
                    </DropdownItem>
                    <DropdownItem key="logout" onClick={doLogout}>
                      Log out
                    </DropdownItem>
                  </DropdownList>
                </Dropdown>
              </ToolbarItem>
            </ToolbarGroup>
          </ToolbarContent>
        </Toolbar>
      </MastheadContent>
    </Masthead>
  );

  const nav = (
    <Nav aria-label="Main navigation">
      <NavList>
        <NavItem isActive={isActive("/")}>
          <Link to="/">Reviews</Link>
        </NavItem>
        <NavItem isActive={isActive("/tracked")}>
          <Link to="/tracked">Tracked PRs</Link>
        </NavItem>
        <NavItem isActive={isActive("/account")}>
          <Link to="/account">Account</Link>
        </NavItem>
        {isAdmin && (
          <NavExpandable
            title="Admin"
            groupId="admin"
            isActive={pathname.startsWith("/admin")}
            isExpanded
          >
            {ADMIN_LINKS.map((l) => (
              <NavItem key={l.to} groupId="admin" isActive={isActive(l.to)}>
                <Link to={l.to}>{l.label}</Link>
              </NavItem>
            ))}
          </NavExpandable>
        )}
      </NavList>
    </Nav>
  );

  const sidebar = (
    <PageSidebar>
      <PageSidebarBody>{nav}</PageSidebarBody>
    </PageSidebar>
  );

  return (
    <Page
      header={header}
      sidebar={sidebar}
      isManagedSidebar
      skipToContent={<SkipToContent href="#eagle-main">Skip to content</SkipToContent>}
      mainContainerId="eagle-main"
    >
      <Outlet />
    </Page>
  );
}
