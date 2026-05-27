import type { Order } from '../types/trading';

interface Props {
  orders: Order[];
}

export function OrdersTable({ orders }: Props) {
  return (
    <section className="panel table-panel">
      <div className="panel-title">
        <h2>Orders</h2>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Ticker</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Type</th>
              <th>Status</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {orders.length === 0 && (
              <tr>
                <td colSpan={6}>No orders yet.</td>
              </tr>
            )}
            {orders.map((order) => (
              <tr key={order.id}>
                <td className="ticker">{order.ticker}</td>
                <td>{order.side.toUpperCase()}</td>
                <td>{order.qty}</td>
                <td>{order.order_type}</td>
                <td>{order.status}</td>
                <td>{order.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
