// 密码复杂度校验（与后端 auth.validate_password 一致）：≥8 位，含字母和数字
export function validatePassword(pw) {
  if (!pw || pw.length < 8) return false
  if (!/[A-Za-z]/.test(pw)) return false
  if (!/[0-9]/.test(pw)) return false
  return true
}
