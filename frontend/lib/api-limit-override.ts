let oneTimeBatchBypassArmed = false;

export function armOneTimeBatchBypass(): void {
  oneTimeBatchBypassArmed = true;
}

export function consumeOneTimeBatchBypass(): boolean {
  const value = oneTimeBatchBypassArmed;
  oneTimeBatchBypassArmed = false;
  return value;
}
