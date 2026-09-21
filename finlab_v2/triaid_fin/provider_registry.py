from __future__ import annotations


class ProviderRegistry:
    version="provider-registry@0.2.0"

    def __init__(self)->None:
        self._providers:dict[str,object]={}
        self._routes:dict[str,list[str]]={}

    def register(self,name:str,provider,*,routes:list[str]|tuple[str,...]=())->None:
        if not name:
            raise ValueError("provider name is required")
        self._providers[name]=provider
        for route in routes:
            self.route(route,name)

    def route(self,capability:str,provider_name:str)->None:
        if provider_name not in self._providers:
            raise KeyError(f"provider_not_registered:{provider_name}")
        self._routes[str(capability).upper()]=[provider_name]

    def add_fallback(self,capability:str,provider_name:str)->None:
        if provider_name not in self._providers:
            raise KeyError(f"provider_not_registered:{provider_name}")
        key=str(capability).upper()
        chain=self._routes.setdefault(key,[])
        if provider_name not in chain:
            chain.append(provider_name)

    def unroute(self,capability:str)->None:
        self._routes.pop(str(capability).upper(),None)

    def provider_for(self,capability:str):
        chain=self._routes.get(str(capability).upper()) or []
        name=chain[0] if chain else None
        return self._providers.get(name) if name else None

    def providers_for(self,capability:str)->list[object]:
        chain=self._routes.get(str(capability).upper()) or []
        return [self._providers[name] for name in chain if name in self._providers]

    def provider(self,name:str):
        return self._providers.get(name)

    def status(self)->dict:
        providers={}
        for name,provider in self._providers.items():
            configured=getattr(provider,"configured",True)
            providers[name]={
                "version":getattr(provider,"version",type(provider).__name__),
                "configured":bool(configured),
            }
        return {
            "version":self.version,
            "providers":providers,
            "routes":{key:(chain[0] if chain else None) for key,chain in self._routes.items()},
            "chains":{key:list(chain) for key,chain in self._routes.items()},
        }
