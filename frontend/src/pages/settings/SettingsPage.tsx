import { useEffect, useState } from 'react';
import { Save, Store, Printer, Shield, Moon, Sun, MonitorSmartphone } from 'lucide-react';
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
  Skeleton,
} from '../../components/ui/index.ts';
import { useAuth } from '../../context/AuthContext.tsx';
import { useTheme } from '../../context/ThemeContext.tsx';
import { getShopSettings, updateShopSettings } from '../../lib/api/shops.ts';

export function SettingsPage() {
  const { user, isClerkConfigured } = useAuth();
  const { theme, setTheme } = useTheme();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [name, setName] = useState('');
  const [address, setAddress] = useState('');
  const [phone, setPhone] = useState('');
  const [taxId, setTaxId] = useState('');
  const [defaultUnit, setDefaultUnit] = useState('meters');
  const [currency, setCurrency] = useState('PKR');

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const data = await getShopSettings();
        if (cancelled) return;
        setName(data.name ?? '');
        setAddress(data.address ?? '');
        setPhone(data.phone ?? '');
        setTaxId(data.tax_id ?? '');
        setDefaultUnit(data.default_unit ?? 'meters');
        setCurrency(data.currency ?? 'PKR');
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load shop settings');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSave = async () => {
    if (!name.trim()) {
      setError('Store / Shop Name must not be blank.');
      return;
    }
    try {
      setSaving(true);
      setError(null);
      setSaved(false);
      const data = await updateShopSettings({
        name: name.trim(),
        address: address.trim(),
        phone: phone.trim(),
        tax_id: taxId.trim(),
        default_unit: defaultUnit,
        currency,
      });
      setName(data.name ?? '');
      setAddress(data.address ?? '');
      setPhone(data.phone ?? '');
      setTaxId(data.tax_id ?? '');
      setDefaultUnit(data.default_unit ?? 'meters');
      setCurrency(data.currency ?? 'PKR');
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save shop settings');
    } finally {
      setSaving(false);
    }
  };

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
            onClick={handleSave}
            disabled={loading || saving}
          >
            {saving ? 'Saving…' : 'Save Changes'}
          </Button>
        }
      />

      {error && (
        <Alert variant="danger" title="Could not save settings">
          {error}
        </Alert>
      )}

      {saved && (
        <Alert variant="success" title="Settings Updated">
          Your shop configuration has been updated successfully.
        </Alert>
      )}

      {/* Shop Profile */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Store className="w-4 h-4 text-zinc-700 dark:text-zinc-300" />
            <CardTitle className="text-sm font-semibold">Store Information</CardTitle>
          </div>
          <CardDescription>
            These details appear on customer thermal receipts and invoices.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3.5">
          {loading ? (
            <>
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Skeleton className="h-9 w-full" />
                <Skeleton className="h-9 w-full" />
              </div>
            </>
          ) : (
            <>
              <Input
                label="Store / Shop Name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="KapraOS Fabrics & Suiting"
              />
              <Input
                label="Physical Address"
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="Shop #14, Cloth Market, Faisalabad"
              />
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <Input
                  label="Phone / WhatsApp Contact"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="0300-8765432"
                />
                <Input
                  label="NTN / Tax ID (Optional)"
                  value={taxId}
                  onChange={(e) => setTaxId(e.target.value)}
                  placeholder="e.g. 1234567-8"
                />
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Fabric Retail Preferences */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Printer className="w-4 h-4 text-zinc-700 dark:text-zinc-300" />
            <CardTitle className="text-sm font-semibold">Operational Defaults</CardTitle>
          </div>
          <CardDescription>
            Default units and precision for inventory cutting and counter sales.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3.5">
          {loading ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Skeleton className="h-9 w-full" />
              <Skeleton className="h-9 w-full" />
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <Select
                label="Primary Fabric Unit"
                value={defaultUnit}
                onChange={(e) => setDefaultUnit(e.target.value)}
              >
                <option value="meters">Meters (m)</option>
                <option value="yards">Yards (gazz)</option>
                <option value="pieces">Pieces / Suits</option>
              </Select>
              <Select
                label="Operational Currency"
                value={currency}
                onChange={(e) => setCurrency(e.target.value)}
              >
                <option value="PKR">Pakistani Rupee (PKR - Rs.)</option>
              </Select>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Security & Tenant Identity */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <MonitorSmartphone className="w-4 h-4 text-zinc-700 dark:text-zinc-300" />
            <CardTitle className="text-sm font-semibold">Appearance</CardTitle>
          </div>
          <CardDescription>
            Switch between light and dark mode. Your choice is saved on this device.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-3">
            {(
              [
                { value: 'light', label: 'Light', icon: Sun },
                { value: 'dark', label: 'Dark', icon: Moon },
              ] as const
            ).map((option) => {
              const Icon = option.icon;
              const isActive = theme === option.value;
              return (
                <button
                  key={option.value}
                  type="button"
                  onClick={() => setTheme(option.value)}
                  aria-pressed={isActive}
                  className={`flex items-center gap-2.5 p-3 rounded-md border transition-colors cursor-pointer ${
                    isActive
                      ? 'border-zinc-900 dark:border-zinc-100 bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900'
                      : 'border-zinc-200 dark:border-zinc-800 hover:border-zinc-300 dark:hover:border-zinc-700 hover:bg-zinc-50/80 dark:hover:bg-zinc-800/60'
                  }`}
                >
                  <Icon className="w-4 h-4 shrink-0" />
                  <div className="text-left">
                    <div className="text-xs font-semibold">{option.label}</div>
                    <div className={`text-[10px] ${isActive ? 'opacity-70' : 'text-zinc-500 dark:text-zinc-400'}`}>
                      {option.value === 'light' ? 'Bright storefront' : 'Easy on eyes at night'}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
          <p className="mt-3 text-[11px] text-zinc-500 dark:text-zinc-400">
            Tip: you can also toggle instantly from the sun / moon button in the top header.
            First visit follows your OS preference.
          </p>
        </CardContent>
      </Card>

      {/* Security & Tenant Identity */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Shield className="w-4 h-4 text-zinc-700 dark:text-zinc-300" />
            <CardTitle className="text-sm font-semibold">Tenant Context & Auth</CardTitle>
          </div>
          <CardDescription>
            Shop isolation verified server-side through Clerk authentication.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-2 text-xs text-zinc-600 dark:text-zinc-400">
          <div className="flex justify-between py-1 border-b border-zinc-100 dark:border-zinc-800">
            <span className="font-medium text-zinc-700 dark:text-zinc-300">Authenticated User ID</span>
            <span className="font-mono text-zinc-500 dark:text-zinc-400">{user?.id || 'usr_local_dev'}</span>
          </div>
          <div className="flex justify-between py-1 border-b border-zinc-100 dark:border-zinc-800">
            <span className="font-medium text-zinc-700 dark:text-zinc-300">Active Tenant Shop ID</span>
            <span className="font-mono text-zinc-500 dark:text-zinc-400">{user?.shopId || 'shop_default_local'}</span>
          </div>
          <div className="flex justify-between py-1">
            <span className="font-medium text-zinc-700 dark:text-zinc-300">Clerk Production Integration</span>
            <span className="font-medium text-zinc-900 dark:text-zinc-100">
              {isClerkConfigured ? 'Enabled & Connected' : 'Local Fallback Mode'}
            </span>
          </div>
        </CardContent>
      </Card>
    </PageContainer>
  );
}
