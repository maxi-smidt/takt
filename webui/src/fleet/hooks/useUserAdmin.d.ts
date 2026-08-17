import type { AdminUser } from "../components/AccessModal";

interface UseUserAdminArgs {
  csrf: string;
}

export function useUserAdmin(args: UseUserAdminArgs): {
  users: AdminUser[];
  error: string;
  temporaryPassword: string;
  load: () => Promise<void>;
  create: (username: string) => Promise<boolean>;
  changeState: (user: AdminUser) => Promise<void>;
  reset: (user: AdminUser) => Promise<void>;
};
