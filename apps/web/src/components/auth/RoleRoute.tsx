import { Outlet } from 'react-router-dom'
import { AccessDeniedPage } from '@/pages/AccessDeniedPage'
import { useStore } from '@/store/useStore'
import type { UserRole } from '@/types'

/** Keeps every route in a role-owned subtree behind the same boundary. */
export function RoleRoute({ roles }: { roles: readonly UserRole[] }) {
  const role = useStore((state) => state.user?.role)

  if (!role || !roles.includes(role)) return <AccessDeniedPage />

  return <Outlet />
}
