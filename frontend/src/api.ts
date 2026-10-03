export async function api<T>(path:string, options:RequestInit = {}):Promise<T> {
  const response = await fetch('/api'+path,{...options,headers:{'Content-Type':'application/json','X-Simulation-Token':sessionStorage.getItem('simulationToken') || '',...options.headers}});
  if (!response.ok) {const body = await response.json().catch(()=>({detail:'Connection unavailable'})); throw new Error(typeof body.detail === 'string'?body.detail:'Please check the information and try again.');}
  return response.json();
}
export const post = <T>(path:string,data:unknown={})=>api<T>(path,{method:'POST',body:JSON.stringify(data)});
