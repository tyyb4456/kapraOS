import { useState } from 'react';
import { Save, Store, Printer, Shield } from 'lucide-react';
import {
  PageContainer,
  PageHeader,
} from '../../components/layout/index.ts';
import {
  Button,
  Input,
  Select,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
  Alert,
} from '../../components/ui/index.ts';
import { useAuth } from '../../context/AuthContext.tsx';

export function SettingsPage() {
  const { user, isClerkConfigured } = useAuth();
  const [saved, setSaved] = useState(false);

  return (
    <PageContainer maxWidth="narrow">
      <PageHeader
        title="Shop & System Settings"
        description="Configure store profile, default fabric measurement units, currency, and receipt options."
        breadcrumbs={[
          { label: 'Settings' },
        ]}
        actions={
          <Button
            variant="primary"
            size="sm"
            leftIcon={<Save className="w-4 h-4" />}
            onClick={() => {
              setSaved(true);
              setTimeout(() => setSaved(false), 3000);
            }}
          >
            Save Changes
          </Button>
        }
      />

      {saved && (
        <Alert variant="success" title="Settings Updated">
          Your shop configuration has been updated successfully.
        </Alert>
      )}

      {/* Shop Profile */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Store className="w-4 h-4 text-zinc-700" />
            <CardTitle className="text-sm font-semibold">Store Information</CardTitle>
          </div>
          <CardDescription>
            These details appear on customer thermal receipts and invoices.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3.5">
          <Input label="Store / Shop Name" defaultValue="KapraOS Fabrics & Suiting" />
          <Input label="Physical Address" defaultValue="Shop #14, Cloth Market, Faisalabad" />
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Input label="Phone / WhatsApp Contact" defaultValue="0300-8765432" />
            <Input label="NTN / Tax ID (Optional)" placeholder="e.g. 1234567-8" />
          </div>
        </CardContent>
      </Card>

      {/* Fabric Retail Preferences */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Printer className="w-4 h-4 text-zinc-700" />
            <CardTitle className="text-sm font-semibold">Operational Defaults</CardTitle>
          </div>
          <CardDescription>
            Default units and precision for inventory cutting and counter sales.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3.5">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <Select label="Primary Fabric Unit" defaultValue="meters">
              <option value="meters">Meters (m)</option>
              <option value="yards">Yards (gazz)</option>
              <option value="pieces">Pieces / Suits</option>
            </Select>
            <Select label="Operational Currency" defaultValue="PKR">
              <option value="PKR">Pakistani Rupee (PKR - Rs.)</option>
            </Select>
          </div>
        </CardContent>
      </Card>

      {/* Security & Tenant Identity */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Shield className="w-4 h-4 text-zinc-700" />
            <CardTitle className="text-sm font-semibold">Tenant Context & Auth</CardTitle>
          </div>
          <CardDescription>
            Shop isolation verified server-side through Clerk authentication.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-xs text-zinc-600">
          <div className="flex justify-between py-1 border-b border-zinc-100">
            <span className="font-medium text-zinc-700">Authenticated User ID</span>
            <span className="font-mono text-zinc-500">{user?.id || 'usr_local_dev'}</span>
          </div>
          <div className="flex justify-between py-1 border-b border-zinc-100">
            <span className="font-medium text-zinc-700">Active Tenant Shop ID</span>
            <span className="font-mono text-zinc-500">{user?.shopId || 'shop_default_local'}</span>
          </div>
          <div className="flex justify-between py-1">
            <span className="font-medium text-zinc-700">Clerk Production Integration</span>
            <span className="font-medium text-zinc-900">
              {isClerkConfigured ? 'Enabled & Connected' : 'Local Fallback Mode'}
            </span>
          </div>
        </CardContent>
      </Card>
    </PageContainer>
  );
}
