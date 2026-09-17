import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';

const origin = Platform.OS === 'web' ? '' : 'https://mapilla.net';
const database = Platform.OS === 'web'
    ? (new URLSearchParams(window.location.search).get('db') || 'yasser3') : 'yasser3';
let nativeToken: string | null = null;
export class ApiError extends Error {
    constructor(message: string, public auth = false) { super(message); }
}
export async function restoreToken() {
    if (Platform.OS !== 'web') nativeToken = await SecureStore.getItemAsync('mapilla.session');
}
export async function call(operation: string, params: any = {}): Promise<any> {
    let response: Response;
    try {
        response = await fetch(`${origin}/supervisor-app/api/${operation}`, {
            method: 'POST', credentials: 'include',
            headers: { 'Content-Type': 'application/json', 'X-Mapilla-App': '1',
                ...(nativeToken ? { Authorization: `Bearer ${nativeToken}` } : {}) },
            body: JSON.stringify({ jsonrpc: '2.0', method: 'call', params: { db: database, ...params }, id: 1 }),
        });
    } catch { throw new ApiError('تعذّر الاتصال بالمصنع. لم يتم تأكيد العملية؛ حدّث البيانات قبل إعادة المحاولة.'); }
    if (!response.ok) throw new ApiError('النظام غير متاح مؤقتًا. حاول مرة أخرى.');
    const body = await response.json();
    if (body.error) {
        const name = body.error.data?.name || '';
        const auth = name.endsWith('AccessDenied') || name.endsWith('SessionExpiredException');
        const message = body.error.data?.message || 'تعذّر تنفيذ الطلب.';
        throw new ApiError(message === 'Access Denied' ? 'اسم المستخدم أو كلمة المرور غير صحيحة، أو انتهت الجلسة.' : message, auth);
    }
    return body.result;
}
export async function login(login: string, password: string, otp: string) {
    const result = await call('login', { login, password, otp, native: Platform.OS !== 'web' });
    if (result.token) {
        await SecureStore.setItemAsync('mapilla.session', result.token);
        nativeToken = result.token;
    }
    return result;
}
export async function logout() {
    await call('logout');
    if (Platform.OS !== 'web') await SecureStore.deleteItemAsync('mapilla.session');
    nativeToken = null;
}
export const requestId = () => `${Date.now()}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
