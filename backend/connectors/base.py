from typing import Protocol, Any
from backend.models.domain import Episode, uid
from backend.services.finance import PolicyError, require

OPERATIONS = {
    'gnani': {'transcribe_audio', 'synthesize_voice'},
    'pine_labs': {'get_transaction', 'check_payment_status', 'create_payment_link'},
    'delhivery': {'get_shipment_status', 'standardize_address'},
    'messaging': {'send_message'},
}

class Connector(Protocol):
    async def call(self, episode: Episode, operation: str, arguments: dict[str, Any], key: str) -> dict[str, Any]: ...

class WizardConnector:
    def __init__(self, name: str): self.name = name
    async def call(self, episode, operation, arguments, key):
        require(operation in OPERATIONS[self.name], 'Operation outside rail scope')
        if self.name in episode.connector_failures:
            raise PolicyError(f'{self.name} unavailable: {episode.connector_failures[self.name]}')
        for response in reversed(episode.connector_responses):
            if response['connector'] == self.name and response['operation'] == operation and response['request_key'] == key:
                return {'mode': 'wizard', **response['result']}
        if operation == 'create_payment_link':
            return {'mode':'wizard', 'reference': key, 'url': None, 'status':'SIMULATED_REQUEST_CREATED'}
        if operation == 'send_message':
            return {'mode':'wizard', 'status':'SIMULATED_DELIVERY', 'message_id': uid('message')}
        return {'mode':'wizard', 'status':'PENDING_EXTERNAL_RESPONSE', 'request_key':key,
                'notice':'Supply a documented connector response in the external-world console.'}

class UnconfiguredRealConnector:
    def __init__(self, name: str): self.name = name
    async def call(self, episode, operation, arguments, key):
        raise PolicyError(f'Real {self.name} adapter requires verified API documentation and credentials; no endpoint is invented.')

class Rails:
    def __init__(self, mode='wizard'):
        factory = WizardConnector if mode == 'wizard' else UnconfiguredRealConnector
        self.adapters = {name: factory(name) for name in OPERATIONS}
        self.messaging = WizardMessagingConnector(self) if mode == 'wizard' else RailMessagingConnector(self)
    async def call(self, e, connector, operation, arguments, key):
        require(connector in self.adapters, 'Unknown connector')
        return await self.adapters[connector].call(e, operation, arguments, key)


class MessagingConnector(Protocol):
    async def send_message(self, episode: Episode, recipient: str, message: str, obligation_id: str) -> dict[str, Any]: ...


class RailMessagingConnector:
    def __init__(self, rails): self.rails=rails
    async def send_message(self, episode, recipient, message, obligation_id):
        return await self.rails.call(episode,'messaging','send_message',{'recipient':recipient,'message':message,'obligation_id':obligation_id},uid('reminder'))


class WizardMessagingConnector(RailMessagingConnector):
    """Simulates only the external delivery rail, never LLM reasoning."""
    async def send_message(self, episode, recipient, message, obligation_id):
        result=await super().send_message(episode,recipient,message,obligation_id)
        require(result.get('status') in ('SIMULATED_DELIVERY','MESSAGE_READY','PENDING_EXTERNAL_RESPONSE'),'Wizard cannot attest real delivery')
        return result
