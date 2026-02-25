type SuccessAlertProps = {
  message: string;
};

export function SuccessAlert({ message }: SuccessAlertProps) {
  if (!message) return null;
  return (
    <div role="status" className="mb-4 rounded-md border border-success-border bg-success-bg px-4 py-3 text-sm text-success">
      {message}
    </div>
  );
}
