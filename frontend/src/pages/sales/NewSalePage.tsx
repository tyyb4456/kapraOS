import { useState } from 'react';
import { Trash2, CheckCircle2, ArrowLeft } from 'lucide-react';
import { Link } from 'react-router-dom';
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
  Badge,
} from '../../components/ui/index.ts';
import { formatCurrency, formatQuantity } from '../../lib/formatters.ts';

export function NewSalePage() {
  const [paymentMethod, setPaymentMethod] = useState('cash');

  return (
    <PageContainer maxWidth="full">
      <div className="flex items-center gap-2 mb-2">
        <Link
          to="/sales"
          className="inline-flex items-center gap-1 text-xs text-zinc-500 hover:text-zinc-900 transition-colors"
        >
          <ArrowLeft className="w-3.5 h-3.5" />
          Back to Sales History
        </Link>
      </div>

      <PageHeader
        title="POS — New Fabric Counter Sale"
        description="Select cloth rolls, enter cut meters or piece count, and issue customer bill."
      />

      {/* POS Two Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left 2 Cols: Item Picker and Sale Cart */}
        <div className="lg:col-span-2 space-y-4">
          {/* Customer & Price Mode Selector */}
          <Card>
            <CardContent className="p-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <Select label="Customer Account">
                    <option value="walk_in">Walk-in Customer (Cash)</option>
                    <option value="c1">Chaudhry Fabric Traders (Khata)</option>
                    <option value="c2">Haji Muhammad & Sons (Khata)</option>
                  </Select>
                </div>
                <div>
                  <Input label="Customer Phone (Optional)" placeholder="0300-1234567" />
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Cart Table Shell */}
          <Card>
            <CardHeader className="p-4 pb-2 border-b border-zinc-100 flex flex-row items-center justify-between">
              <CardTitle className="text-xs font-semibold uppercase text-zinc-500 tracking-wider">
                Sale Items & Fabric Cuts
              </CardTitle>
              <Badge variant="neutral" size="sm">
                2 Items
              </Badge>
            </CardHeader>
            <CardContent className="p-0">
              <div className="divide-y divide-zinc-100 text-xs">
                {/* Item 1 */}
                <div className="p-4 flex items-center justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <h4 className="font-semibold text-zinc-900 truncate">
                      Egyptian Lawn - Plain Dyed
                    </h4>
                    <p className="text-[11px] text-zinc-500 font-mono">
                      COT-LAWN-01-NAVY • Navy Blue
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="text-right">
                      <span className="font-medium text-zinc-900 block font-tabular">
                        {formatQuantity(4.5, 'meters')}
                      </span>
                      <span className="text-[10px] text-zinc-500 font-tabular">
                        @ {formatCurrency(950)} / m
                      </span>
                    </div>
                    <div className="text-right font-semibold text-zinc-900 font-tabular w-20">
                      {formatCurrency(4.5 * 950)}
                    </div>
                    <button
                      type="button"
                      className="text-zinc-400 hover:text-rose-600 p-1 rounded"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>

                {/* Item 2 */}
                <div className="p-4 flex items-center justify-between gap-4">
                  <div className="flex-1 min-w-0">
                    <h4 className="font-semibold text-zinc-900 truncate">
                      Italian Wool Blend 120s
                    </h4>
                    <p className="text-[11px] text-zinc-500 font-mono">
                      SUIT-WOL-04-BLK • Jet Black
                    </p>
                  </div>
                  <div className="flex items-center gap-3">
                    <div className="text-right">
                      <span className="font-medium text-zinc-900 block font-tabular">
                        {formatQuantity(4.0, 'meters')}
                      </span>
                      <span className="text-[10px] text-zinc-500 font-tabular">
                        @ {formatCurrency(3400)} / m
                      </span>
                    </div>
                    <div className="text-right font-semibold text-zinc-900 font-tabular w-20">
                      {formatCurrency(4.0 * 3400)}
                    </div>
                    <button
                      type="button"
                      className="text-zinc-400 hover:text-rose-600 p-1 rounded"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Right Col: Bill Summary & Checkout */}
        <div className="space-y-4">
          <Card className="sticky top-20">
            <CardHeader className="p-4 border-b border-zinc-100">
              <CardTitle className="text-sm font-semibold text-zinc-900">
                Payment Summary
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 space-y-4">
              <div className="space-y-2 text-xs border-b border-zinc-100 pb-3">
                <div className="flex justify-between text-zinc-600">
                  <span>Gross Subtotal</span>
                  <span className="font-tabular font-medium text-zinc-900">
                    {formatCurrency(17875)}
                  </span>
                </div>
                <div className="flex justify-between text-zinc-600">
                  <span>Discount</span>
                  <span className="font-tabular text-zinc-600">
                    {formatCurrency(0)}
                  </span>
                </div>
                <div className="flex justify-between text-sm font-bold text-zinc-900 pt-1">
                  <span>Net Payable</span>
                  <span className="font-tabular text-base text-zinc-900">
                    {formatCurrency(17875)}
                  </span>
                </div>
              </div>

              {/* Payment Method Selector */}
              <div className="space-y-2">
                <label className="block text-xs font-medium text-zinc-700">
                  Settlement Method
                </label>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setPaymentMethod('cash')}
                    className={`p-2.5 rounded-md border text-xs font-medium text-center transition-colors cursor-pointer ${
                      paymentMethod === 'cash'
                        ? 'border-zinc-900 bg-zinc-900 text-white shadow-xs'
                        : 'border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50'
                    }`}
                  >
                    Cash
                  </button>
                  <button
                    type="button"
                    onClick={() => setPaymentMethod('khata')}
                    className={`p-2.5 rounded-md border text-xs font-medium text-center transition-colors cursor-pointer ${
                      paymentMethod === 'khata'
                        ? 'border-zinc-900 bg-zinc-900 text-white shadow-xs'
                        : 'border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50'
                    }`}
                  >
                    Customer Khata
                  </button>
                </div>
              </div>

              <Button
                variant="primary"
                size="lg"
                className="w-full text-sm font-semibold mt-2"
                leftIcon={<CheckCircle2 className="w-4 h-4" />}
              >
                Complete Sale ({formatCurrency(17875)})
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </PageContainer>
  );
}
