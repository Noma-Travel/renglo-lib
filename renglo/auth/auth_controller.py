#auth_controller.py
from flask import Flask, request, redirect, session, url_for,Blueprint, jsonify

import boto3
import copy
import json
import logging
from datetime import datetime
from ..common import *
import uuid
from decimal import Decimal
from renglo.auth.auth_model import AuthModel
import re
import time
from validate_email import validate_email


class AuthController:

    def __init__(self, config=None, tid=False, ip=False):
        self.config = config or {}
        self.AUM = AuthModel(config=self.config)
        self._invocation_user_id = None
        self._invocation_jwt_claims = None
        # Set up logger
        self.logger = logging.getLogger(self.__class__.__name__)
        # Configure logger if not already configured (prevents duplicate handlers)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.DEBUG)
        
        
    def refresh_tree(self):
    
        # Generate tree
        self.logger.error(f"Refreshing tree")
        data = {}
        data['user_id'] = self.get_current_user()
        response = self.get_tree_full(**data)

        # Initialize S3 client and define bucket name and file path
        s3_client = boto3.client('s3')  # Ensure boto3 is imported
        bucket_name = self.config.get('S3_BUCKET_NAME')  
        file_path = f'auth/tree/{data["user_id"]}'

        # Store the new version in S3
        try:
            s3_client.put_object(Bucket=bucket_name, Key=file_path, Body=json.dumps(response['document']))
        except Exception as e:
            self.logger.error(f"Failed to upload to S3: {str(e)}")
            return jsonify({"success": False, "message": "Failed to upload to S3", "status": 500}), 500
        
        return response
        
        
        
    
    def set_invocation_user(self, user_id):
        """Set the current user for this invocation (e.g. from payload in Lambda/Docker)."""
        self._invocation_user_id = user_id

    def set_invocation_jwt_claims(self, jwt_claims):
        """Set JWT claims for this invocation; get_current_user() will derive user_id from them."""
        self._invocation_jwt_claims = jwt_claims

    @staticmethod
    def _user_id_from_claims(claims):
        if not claims:
            return None
        if "sub" in claims:
            return create_md5_hash(claims["sub"], 9)
        if "cognito:username" in claims:
            return create_md5_hash(claims["cognito:username"], 9)
        if "username" in claims:
            return create_md5_hash(claims["username"], 9)
        if "email" in claims:
            return create_md5_hash(claims["email"], 9)
        return None

    def get_current_user(self, jwt_claims=None):
        """
        Return the current user id. Precedence:
        1. jwt_claims argument (if provided)
        2. _invocation_user_id (set by set_invocation_user for Lambda/Docker)
        3. _invocation_jwt_claims (set from payload)
        4. Flask request: g.current_user_id (set by API when using cognito_auth_required)
        5. Optional: flask_cognito current_cognito_jwt (lazy import, when running in Flask app context)
        """
        if jwt_claims is not None:
            uid = self._user_id_from_claims(jwt_claims)
            if uid is not None:
                return uid
        if getattr(self, "_invocation_user_id", None) is not None:
            return self._invocation_user_id
        if getattr(self, "_invocation_jwt_claims", None) is not None:
            uid = self._user_id_from_claims(self._invocation_jwt_claims)
            if uid is not None:
                return uid
        try:
            from flask import g
            uid = getattr(g, "current_user_id", None)
            if uid is not None:
                return uid
        except Exception:
            pass
        try:
            from flask_cognito import current_cognito_jwt
            return self._user_id_from_claims(dict(current_cognito_jwt) if current_cognito_jwt else None)
        except Exception:
            pass
        return None


    def _invite_fe_base_url(self):
        """Public frontend origin for invite/login links (never the API BASE_URL alone)."""
        module_base = globals().get('BASE_URL') or ''
        return (
            (self.config.get('APP_FE_BASE_URL') or '').strip()
            or (self.config.get('FE_BASE_URL') or '').strip()
            or (module_base or '').strip()
            or (self.config.get('BASE_URL') or '').strip()
            or 'http://127.0.0.1:3000'
        )

    def _invite_from_address(self):
        return (
            (self.config.get('INVITE_FROM') or '').strip()
            or (self.config.get('SES_INVITE_SENDER') or '').strip()
            or 'Noma <noreply@email.travelwithnoma.com>'
        )

    def invite_user(self,email,team_id,portfolio_id,sender_id,locale='en'):
        '''
        Invites user to a team

        INPUT
        - email
        - team_id
        - portfolio_id
        - locale (optional): 'pt' or 'en' for localized invite email

        Returns email_sent / invite_url when an email invite is attempted so callers
        (e.g. invite_attendant) can offer a copy-link fallback on delivery failure.
        '''
        # 1. Check whether the email is valid
        #This function is not working. It is rejecting valid emails
        '''
        if not validate_email(email, verify=True):
            return{
                "success":False, 
                "message": "Email is not valid", 
                "status" :400
                }
        '''

        #1b. Check if the recipient email is already part of the team
        

        #2. Check if the recipient email already exists in the user pool
        response_1 = self.get_user_id(email)
        self.logger.debug('User exists in pool?:'+str(response_1))
        
        if response_1['success']:

            #3. Check if the recipient already belongs to the portfolio.
            user_id = response_1['document']['user_id']
            check = self.is_user_team_same_portfolio(
                user_id=user_id,
                team_id=team_id
                )
            
            if check:       
                self.logger.debug('User already belongs to the portfolio:'+str(check))
                # If yes, the user will be automatically added to the team without sending an invite
                response = self.add_user_to_team_funnel(user_id=user_id,team_id=team_id)
                if response['success']:
                    self.logger.debug('User added to the team:'+str(response))
                    return{
                        "success":True, 
                        "message": "User has been added to the team.", 
                        "status" : 200,
                        "email_sent": False,
                        "invite_url": None,
                        }
                else:
                    self.logger.debug('User could not be added to the team:'+str(response))
                    return{
                        "success":False, 
                        "message": "User could not be added to the team.", 
                        "status" :400,
                        "email_sent": False,
                        "invite_url": None,
                        }
            
                
            else:
                # If not, the user will still get added to the team
                self.logger.debug('Email found in the user pool but it does not belong to the portfolio. Adding new user to Team')
                response = self.add_user_to_team_funnel(user_id=user_id,team_id=team_id)
                if response['success']:
                    return{
                        "success":True, 
                        "message": "User has been added to the team and portfolio.", 
                        "status" : 200,
                        "email_sent": False,
                        "invite_url": None,
                        }
                else:
                    return{
                        "success":False, 
                        "message": "User could not be added to the team and portfolio.", 
                        "status" :400,
                        "email_sent": False,
                        "invite_url": None,
                        }
        
        else:
            # Email not found in the user pool
            self.logger.debug('Email not found in the user pool')
            pass


        # USER DOES NOT EXIST IN THE USER POOL — SEND EMAIL INVITE
        response = self.invite_user_funnel(
            email=email,
            team_id=team_id,
            portfolio_id=portfolio_id,
            sender_id=sender_id,
            locale=locale
            )
        
        self.logger.debug('Invite User Funnel > response: '+ str(response))

        # Warning: Never send out the funnel response as it contains the solution to the challenge.
        # (The challenge is to demonstrate that the invitee has access to the inbox.)
        # We DO propagate the funnel's `message` string (e.g. "RESEND_API_KEY is not
        # configured") since it never carries the hash/challenge data — only `document`
        # (the transaction list, containing the hash) does, and we don't forward that.
        # Surface invite_url so the inviter can copy the link if Resend fails.
        invite_url = response.get('invite_url')
        if response['success']:
            return{
                "success":True,
                "message": "Invite has been sent out.",
                "status" : 200,
                "email_sent": True,
                "invite_url": invite_url,
                }
        else:
            real_reason = response.get('message', 'Unknown error')
            self.logger.error('invite_user: invite_user_funnel failed - %s', real_reason)
            return{
                "success":False,
                "message": f"Invite could not be sent out: {real_reason}",
                "status" :400,
                "email_sent": False,
                "invite_url": invite_url,
                }




    def get_user_id(self,email):
        # DO NOT EXPOSE THIS FUNCTION TO API
        response = self.AUM.check_user_by_email(email)
        self.logger.debug('Calling Identity Pool:'+str(response)) 
        
        if response['success']:
            user_id = create_md5_hash(response['document']['cognito_user_id'],9)
            response['document']['user_id'] = user_id

        return response



    def user_portfolios(self,user_id):
        '''
        input:
        'user_id'
        '''
        user_portfolio_list = []
 
        index = 'irn:rel:user:team:' + user_id + ':*'
        rels_user_teams = self.AUM.list_rel(index)     
        for team in rels_user_teams['document']['items']:
            rel_team_id = team['rel']
 
            index = 'irn:rel:team:portfolio:' + rel_team_id  + ':*'
            rels_team_portfolio = self.AUM.list_rel(index)
            for portfolio in rels_team_portfolio['document']['items']:
                
                if portfolio['rel'] not in user_portfolio_list:
                    user_portfolio_list.append(portfolio['rel'])

        return user_portfolio_list


    def is_user_team_org_same_portfolio(self,**kwargs):

        '''
        input:
        'user_id'
        'team_id'
        'org_id'
        '''
        
        user_portfolio_list = self.user_portfolios(kwargs['user_id'])

        for portfolio_id in user_portfolio_list:

            #Check if the team belongs to the portfolio
            response_1 = self.get_rel('team:portfolio',team_id=kwargs['team_id'],portfolio_id=portfolio_id)

            #Check if the org belongs to the portfolio
            response_2 = self.get_entity('org',portfolio_id=portfolio_id,org_id=kwargs['org_id'])

            if response_1['success'] and response_2['success'] :
                #user,team and org belong to portfolio
                return True
            
        return False
    

    def is_user_team_same_portfolio(self,**kwargs):

        '''
        input:
        'user_id'
        'team_id'
        '''
        
        user_portfolio_list = self.user_portfolios(kwargs['user_id'])

        for portfolio_id in user_portfolio_list:

            #Check if the team belongs to the portfolio
            response_1 = self.get_rel('team:portfolio',team_id=kwargs['team_id'],portfolio_id=portfolio_id)

            
            if response_1['success'] :
                #user and team belong to same portfolio
                return True
            
        return False
            


    def is_global_admin(self, cognito_groups=None, user_id=None):
        """Platform admin: Cognito group global_admin or user entity slot_d."""
        if cognito_groups:
            groups = cognito_groups if isinstance(cognito_groups, list) else [cognito_groups]
            if 'global_admin' in groups:
                return True
        if user_id:
            user_entity = self.get_entity('user', user_id=user_id)
            if user_entity.get('success'):
                doc = user_entity.get('document') or {}
                if doc.get('slot_d') == 'global_admin':
                    return True
        return False

    def _list_all_entities(self, index, limit=50):
        """Paginate list_entity until all items for an index are loaded."""
        all_items = []
        start_key = None
        while True:
            response = self.AUM.list_entity(index, limit=limit, lastkey=start_key)
            if not response.get('success'):
                break
            doc = response.get('document') or {}
            all_items.extend(doc.get('items') or [])
            lek = doc.get('lastkey')
            if not lek:
                break
            if isinstance(lek, dict):
                start_key = lek.get('ref')
            else:
                start_key = lek
        return all_items

    def get_tree_global_admin(self, **kwargs):
        """
        Build auth tree with every portfolio, org, and tool (no team membership required).
        """
        user_id = kwargs.get('user_id')
        tree = {
            'user_id': user_id,
            'is_global_admin': True,
            'portfolios': {},
        }
        self.logger.debug('GENERATING GLOBAL ADMIN TREE')

        portfolio_items = self._list_all_entities('irn:entity:portfolio:*')
        for portfolio in portfolio_items:
            portfolio_id = portfolio.get('_id')
            if not portfolio_id:
                continue

            portfolio_doc = {
                'portfolio_id': portfolio_id,
                'name': portfolio.get('name', portfolio_id),
                'teams': {},
                'orgs': {},
                'tools': {},
            }
            tree['portfolios'][portfolio_id] = portfolio_doc

            tool_ids = []
            tool_items = self._list_all_entities(
                'irn:entity:portfolio/tool:' + portfolio_id + '/*'
            )
            for tool in tool_items:
                tool_id = tool.get('_id')
                if not tool_id:
                    continue
                tool_ids.append(tool_id)
                portfolio_doc['tools'][tool_id] = {
                    'tool_id': tool_id,
                    'name': tool.get('name', tool_id),
                    'handle': tool.get('handle', tool_id),
                    'active': True,
                }

            org_items = self._list_all_entities(
                'irn:entity:portfolio/org:' + portfolio_id + '/*'
            )
            for org in org_items:
                org_id = org.get('_id')
                if not org_id:
                    continue
                portfolio_doc['orgs'][org_id] = {
                    'org_id': org_id,
                    'name': org.get('name', org_id),
                    'handle': org.get('handle', org_id),
                    'active': True,
                    'tools': list(tool_ids),
                }

            team_items = self._list_all_entities(
                'irn:entity:portfolio/team:' + portfolio_id + '/*'
            )
            for team in team_items:
                team_id = team.get('_id')
                if not team_id:
                    continue
                portfolio_doc['teams'][team_id] = {
                    'team_id': team_id,
                    'name': team.get('name', team_id),
                    'handle': team.get('handle', team_id),
                    'tools': {},
                }

        response = {
            'success': True,
            'document': tree,
            'status': 200,
        }
        return response

    def get_tree_full(self,**kwargs):
        # Auth Tree after resolving each document ID 
        # Instead of creating a function to query each entity separately (many functions), 
        # We provide a single endpoint to get all the data the app needs to operate in one call. 
        tree = {}
        tree['user_id'] = kwargs['user_id']
        tree['portfolios'] = {}
        
        


        self.logger.debug('GENERATING TREE')

        # 1. Get all the user:team rels. 
        index = 'irn:rel:user:team:' + kwargs['user_id'] + ':*'
        rels_user_teams = self.AUM.list_rel(index)
        #self.logger.debug('User Teams:'+str(rels_user_teams))

        # 2. For each rel, get its team:portfolio rel. Each rel will show a Portfolio_id
        # Check if rels_user_teams has the expected structure and is not empty
        if (rels_user_teams and 
            'document' in rels_user_teams and 
            'items' in rels_user_teams['document'] and 
            rels_user_teams['document']['items']):
            
            for team in rels_user_teams['document']['items']:
                
                team_id =  team['rel'] 
                self.logger.debug('FLAG0>>TEAM:'+team_id)     
                
                index = 'irn:rel:team:portfolio:' + team['rel'] + ':*'
                rels_team_portfolio = self.AUM.list_rel(index)
                #self.logger.debug('User Portfolios:'+str(rels_team_portfolio))

                # Check if rels_team_portfolio has the expected structure and is not empty
                if (rels_team_portfolio and 
                    'document' in rels_team_portfolio and 
                    'items' in rels_team_portfolio['document'] and 
                    rels_team_portfolio['document']['items']):

                    for portfolio in rels_team_portfolio['document']['items']:

                        portfolio_id = portfolio['rel']
                        self.logger.debug('FLAG1>>TEAM:'+team_id+'PORTFOLIO:'+portfolio_id)

                        if portfolio_id not in tree['portfolios']:

                            #RESOLVE: Get Portfolio entity document
                            portfolio_entity = self.get_entity(
                                'portfolio',
                                portfolio_id=portfolio_id
                                )

                            portfolio_doc = {}  
                            portfolio_doc['portfolio_id'] = portfolio_id      
                            portfolio_doc['name'] = portfolio_entity['document']['name']
                            portfolio_doc['teams'] = {}
                            portfolio_doc['orgs'] = {}

                            #self.logger.debug('Tree: '+str(tree))

                            #self.logger.debug('Inserting Portfolio '+portfolio_id+' in tree'+str(portfolio_doc))
                            tree['portfolios'][portfolio_id] = portfolio_doc

                            #self.logger.debug('Tree: '+str(tree))
                            

                        #Teams
                        #Include Team Document in the tree under the current portfolio

                        #RESOLVE: Get Team entity document
                        team_entity = self.get_entity(
                            'team',
                            portfolio_id=portfolio_id,
                            team_id=team_id
                            )
                        
                        if not team_entity['success']:
                            # Entity doesn't exist
                            # skip the team relationships
                            continue
                        
                        team_doc = {}
                        team_doc['team_id'] = team_id
                        team_doc['name'] = team_entity['document']['name']
                        #team_doc['orgs_access'] = []
                        team_doc['tools'] = {}

                        #self.logger.debug('Inserting Team '+team_id+'in portfolio '+portfolio_id+':'+str(team_doc))
                        tree['portfolios'][portfolio_id]['teams'][team_id] = team_doc


                        
                        #Team to Tools rel
                        index = 'irn:rel:team:tool:' + team_id + ':*'
                        rels_team_tool = self.AUM.list_rel(index)
                        #self.logger.debug('Team Tool rels:'+str(rels_team_org))

                        tools = []
                        # Check if rels_team_tool has the expected structure and is not empty
                        if (rels_team_tool and 
                            'document' in rels_team_tool and 
                            'items' in rels_team_tool['document'] and 
                            rels_team_tool['document']['items']):
                            for tool in rels_team_tool['document']['items']:
                                tools.append(tool['rel'])

                        #self.logger.debug('Inserting tool into team '+team_id+' from portfolio '+portfolio_id+':'+str(tools))
                        tree['portfolios'][portfolio_id]['teams'][team_id]['tools_access'] = tools


                         

                        #Tools

                        # RESOLVE: Get App entity document
                        index = 'irn:entity:portfolio/tool:'+portfolio_id+'/*'
                        entities_tools = self.AUM.list_entity(index)
                        active_orgs = []
                        
                        

                        #self.logger.debug('ENTITIES:'+str(entities_tools))
                        org_tools = {}
                        
                        # Check if entities_tools has the expected structure and is not empty
                        if (entities_tools and 
                            'document' in entities_tools and 
                            'items' in entities_tools['document'] and 
                            entities_tools['document']['items']):
                            
                            for tool in entities_tools['document']['items']:

                                tool_id = tool['_id'] 
                                self.logger.debug('FLAG2>>TEAM:'+team_id+'PORTFOLIO:'+portfolio_id+'TOOL:'+tool_id) 
                               
                                # Tool list at portfolio level
                                if 'tools' not in tree['portfolios'][portfolio_id]:
                                    tree['portfolios'][portfolio_id]['tools'] = {}  # Create 'tools' as an empty dictionary
                                    
                                if tool_id not in tree['portfolios'][portfolio_id]['tools']:
                                    tree['portfolios'][portfolio_id]['tools'][tool_id] = {}
                                    
                                
                                tree['portfolios'][portfolio_id]['tools'][tool_id]['tool_id'] = tool_id
                                tree['portfolios'][portfolio_id]['tools'][tool_id]['name'] = tool['name']
                                tree['portfolios'][portfolio_id]['tools'][tool_id]['handle'] = tool['handle']

                                
                                if 'tools' not in tree['portfolios'][portfolio_id]['teams'][team_id]:
                                    tree['portfolios'][portfolio_id]['teams'][team_id]['tools'] = {}
                                    
                                if tool_id not in tree['portfolios'][portfolio_id]['teams'][team_id]['tools']:
                                    tree['portfolios'][portfolio_id]['teams'][team_id]['tools'][tool_id] = {}
                                    
                                           
                                #Team Tool Roles
                                index = 'irn:rel:team/tool:role:' + team_id + '/' + tool_id + ':*'
                                rels_team_tool_role = self.AUM.list_rel(index)
                                
                                roles = []
                                # Check if rels_team_tool_role has the expected structure and is not empty
                                if (rels_team_tool_role and 
                                    'document' in rels_team_tool_role and 
                                    'items' in rels_team_tool_role['document'] and 
                                    rels_team_tool_role['document']['items']):
                                    for role in rels_team_tool_role['document']['items']:
                                        roles.append(role['rel'])
                                        
                                  
                                tree['portfolios'][portfolio_id]['teams'][team_id]['tools'][tool_id]['roles'] = roles
                                
                                #Team Tool Orgs
                                index = 'irn:rel:team/tool:org:' + team_id + '/' + tool_id + ':*'
                                rels_team_tool_org = self.AUM.list_rel(index)
                                
                                toolorgs = []
                                
                                # Check if rels_team_tool_org has the expected structure and is not empty
                                if (rels_team_tool_org and 
                                    'document' in rels_team_tool_org and 
                                    'items' in rels_team_tool_org['document'] and 
                                    rels_team_tool_org['document']['items']):
                                    
                                    for toolorg in rels_team_tool_org['document']['items']:
                                        
                                        self.logger.debug('FLAG3>>TEAM:'+team_id+'PORTFOLIO:'+portfolio_id+'TOOL:'+tool_id+'TORG:'+toolorg['rel']) 
                                        
                                        
                                        #self.logger.debug('TOOORG:'+toolorg['rel']) 
                                        
                                        
                                        toolorgs.append(toolorg['rel'])
                                        #If there is a team/tool:org rel, the building is active, 
                                        active_orgs.append(toolorg['rel'])
                                         
                                        tree['portfolios'][portfolio_id]['tools'][tool_id]['active'] = True
                                        
                                        #Strategy: 
                                        # 1. Each iteration here tells you whether a team is using a specific tool in a specific organization
                                        # 2. We are going to accumulate that in org_tools and then put it in the tree
                                        
                                        # 3. We check if the org array already exists. We create it if it doesn't
                                        if toolorg['rel'] not in org_tools:
                                            org_tools[toolorg['rel']] = []
                                        #4. We append the tool to the org
                                        org_tools[toolorg['rel']].append(tool_id)
                                        
                                        
                                        
                                        
                                    
                                        
                                tree['portfolios'][portfolio_id]['teams'][team_id]['tools'][tool_id]['orgs'] = toolorgs
                                
                            
                        self.logger.debug('ORG_TOOLS:') 
                        self.logger.debug(org_tools) 
                        
                          
                        #Orgs
                        index = 'irn:entity:portfolio/org:'+portfolio_id+'/*'
                        entities_orgs = self.AUM.list_entity(index)

                        # Check if entities_orgs has the expected structure and is not empty
                        if (entities_orgs and 
                            'document' in entities_orgs and 
                            'items' in entities_orgs['document'] and 
                            entities_orgs['document']['items']):

                            for org in entities_orgs['document']['items']:

                                org_id = org['_id']
                                
                               
                                # Assemble the orgs object
                                if 'orgs' not in tree['portfolios'][portfolio_id]:
                                    tree['portfolios'][portfolio_id]['orgs'] = {}
                                    
                                    
                                if org_id not in tree['portfolios'][portfolio_id]['orgs']:
                                    tree['portfolios'][portfolio_id]['orgs'][org_id] = {}
                                    
                                
                                if 'tools' not in tree['portfolios'][portfolio_id]['orgs'][org_id]:
                                    tree['portfolios'][portfolio_id]['orgs'][org_id]['tools'] = []
                                
                                if org_id in org_tools:    
                                    # Combine the existing tools with the new tools from org_tools
                                    tree['portfolios'][portfolio_id]['orgs'][org_id]['tools'] = list(set(tree['portfolios'][portfolio_id]['orgs'][org_id]['tools'] + org_tools[org_id]))
                                
                                tree['portfolios'][portfolio_id]['orgs'][org_id]['org_id'] = org_id
                                tree['portfolios'][portfolio_id]['orgs'][org_id]['name'] = org['name']
                                tree['portfolios'][portfolio_id]['orgs'][org_id]['handle'] = org['handle']
                                
                                
                                if org_id in active_orgs:
                                    tree['portfolios'][portfolio_id]['orgs'][org_id]['active'] = True     
                                #else:
                                #    tree['portfolios'][portfolio_id]['orgs'][org_id]['active'] = False 
                                


        response = {}
        response['success'] = True
        response['document'] = tree
        response['status'] = 200

        return response



    def list_entity(self,type,**kwargs):

        portfolios = []

        if(type == 'portfolio'): 
            # You can't find the list of portfolios by simply querying the entity table 
            # The reason is that portfolios have no containers. So there is no common index 
            # The only things that groups them are its teams and users. 
            # In order to get a list of Portfolios for a user, you need to:
            # 1. Get all the user:team rels. 
            index = 'irn:rel:user:team:' + kwargs['user_id'] + ':*'
            rel_user_teams = self.AUM.list_rel(index)
            self.logger.debug('User Teams:'+str(rel_user_teams))

            # 2. For each rel, get its team:portfolio rel. Each rel will show a Portfolio_id
            for team in rel_user_teams['document']['items']:
                
                #self.logger.debug('User Teams:'+str(team))
                
                index = 'irn:rel:team:portfolio:' + team['rel'] + ':*'
                rel_team_portfolio = self.AUM.list_rel(index)
                #self.logger.debug('User Portfolios:'+str(rel_team_portfolio))
                portfolios.append(rel_team_portfolio)

            
            response = {}
            response['success'] = True
            response['document'] = portfolios
            response['status'] = 200

            return response
        
        
        if(type =='org'):
            index = 'irn:entity:portfolio/org:'+kwargs['portfolio_id']+'/*'

        if(type =='team'):
            index = 'irn:entity:portfolio/team:'+kwargs['portfolio_id']+'/*'

        if(type =='app'):         
            index = 'irn:entity:team/app:'+kwargs['team_id']+'/*'
            
        if(type =='tool'):         
            index = 'irn:entity:portfolio/tool:'+kwargs['portfolio_id']+'/*'   

        response = self.AUM.list_entity(index) 
        return response




    def get_entity(self,type,**kwargs):

        #if(type== 'user' and 'user_id' in kwargs and 'portfolio_id' in kwargs ):

        missing = False

        if type == 'user':
            if all(key in kwargs for key in ['user_id']):
                index = 'irn:entity:user:*'
                id = kwargs['user_id']
            else:
                missing = True
        
        elif type == 'portfolio':
            if all(key in kwargs for key in ['portfolio_id']):
                index = 'irn:entity:portfolio:*'
                id = kwargs['portfolio_id']
            else:
                missing = True
            
        elif type == 'org':
            if all(key in kwargs for key in ['portfolio_id','org_id']):
                index = 'irn:entity:portfolio/org:'+kwargs['portfolio_id']+'/*'
                id = kwargs['org_id']
            else:
                missing = True

        elif type == 'team':
            if all(key in kwargs for key in ['portfolio_id','team_id']):
                index = 'irn:entity:portfolio/team:'+kwargs['portfolio_id']+'/*'
                id = kwargs['team_id']
            else:
                missing = True

        # The above code snippet is checking if the `type` is equal to 'tool' and if certain keys
        # ('portfolio_id' and 'tool_id') are present in the `kwargs` dictionary. If both conditions
        # are met, it sets the `index` variable to a specific value based on the 'portfolio_id' key,
        # and assigns the value of the 'tool_id' key to the `id` variable. If the conditions are not
        # met, it sets the `missing` variable to True.
        elif type == 'tool':
            if all(key in kwargs for key in ['portfolio_id','tool_id']):        
                index = 'irn:entity:portfolio/tool:'+kwargs['portfolio_id']+'/*'
                id = kwargs['tool_id']
            else:
                missing = True
     
        if missing:
            return {'success':False,'message':'Some parameters are missing','status':500}

        response = self.AUM.get_entity(index,id) 

        result = {}

        if not response['success']:
            result['success'] = False
            result['message'] = response['message'] 
            result['status'] = response['status']               
            return result
        

        result['success'] = True
        result['message'] = response['message']
        result['document'] = response['document']  
        result['status'] = response['status']   
        #self.logger.debug('Returned object:'+str(result))
        return result

       

    def create_entity(self,type,**kwargs):

        self.logger.debug('Data:')
        self.logger.debug(kwargs)

        #Creating new ID for entity
        raw_id = str(uuid.uuid4())
        new_id = create_md5_hash(raw_id,12)

        if(type == 'user'): 
            pk = 'irn:entity:user:*'
            sk = kwargs['user_id']
            irn = 'irn:entity:user:' + kwargs['user_id']
        
        if(type == 'portfolio'): 
            pk = 'irn:entity:portfolio:*'
            sk = new_id
            irn = 'irn:entity:portfolio:' + new_id
            
        elif(type =='org'):
            pk = 'irn:entity:portfolio/org:'+kwargs['portfolio_id']+'/*'
            sk = new_id
            irn = 'irn:entity:portfolio/org:'+kwargs['portfolio_id']+'/'+ new_id

        elif(type =='team'):
            pk = 'irn:entity:portfolio/team:'+kwargs['portfolio_id']+'/*'
            sk = new_id
            irn = 'irn:entity:portfolio/team:'+kwargs['portfolio_id']+'/'+ new_id

        elif(type =='tool'):         
            pk = 'irn:entity:portfolio/tool:'+kwargs['portfolio_id']+'/*'
            sk = new_id
            irn = 'irn:entity:portfolio/tool:'+kwargs['portfolio_id']+'/'+ new_id
 
        '''elif(type =='action'):         
            pk = 'irn:entity:tool/action:'+kwargs['tool_id']+'/*'
            sk = kwargs['action_id']
            irn = 'irn:entity:tool/action:'+kwargs['tool_id']+'/'+ kwargs['action_id']'''
        

        data = {      
            '_id': sk,
            'name': kwargs['name'] if 'name' in kwargs else '',
            'about': kwargs['about'] if 'about' in kwargs else '',
            'handle': kwargs['handle'] if 'handle' in kwargs else '',
            'location': kwargs['location'] if 'location' in kwargs else '',
            'email': kwargs['email'] if 'email' in kwargs else '',
            'slot_a': kwargs['slot_a'] if 'slot_a' in kwargs else '',
            'slot_b': kwargs['slot_b'] if 'slot_b' in kwargs else '',
            'slot_c': kwargs['slot_c'] if 'slot_c' in kwargs else '',
            'slot_d': kwargs['slot_d'] if 'slot_d' in kwargs else '',
            'slot_e': kwargs['slot_e'] if 'slot_e' in kwargs else '',
            'owner_id':kwargs['user_id'] if 'user_id' in kwargs else '',
            'added': datetime.now().isoformat(),
            'is_active': True,
            'type': type,
            'last_ip': kwargs['ip'] if 'ip' in kwargs else '',
            'last_login': datetime.now().isoformat(),
            'modified': datetime.now().isoformat(), 
            'raw_id': raw_id,
            'index':pk,
            'irn':irn,
            'language':kwargs['lan'] if 'lan' in kwargs else ''           
        }

    
        self.logger.debug('Entity to Create:'+str(data))
        response = self.AUM.create_entity(data)

        return response
    



    def unlink_entity(self,**doc):
        '''
        Document is not deleted permanently but marked as unlinked
        Garbage collector should use the "modified" attribute to 
        determine if it is time to delete it permanently
        '''

        self.logger.debug('Document to be unlinked:')
        self.logger.debug(doc)
 
        # Check if the second position exists and replace "entity" with "unentity"
        parts = doc['index'].split(":")
        if len(parts) > 1 and parts[1] == "entity":
            parts[1] = "unentity"
        
        # Join the parts back together with ":"
        undoc = copy.deepcopy(doc)
        undoc['index'] =  ":".join(parts)
        undoc['modified'] = datetime.now().isoformat()
        
        # Create unlinked entity document
        self.logger.debug('Entity to Create:'+str(undoc))
        response_1 = self.AUM.create_entity(undoc)

        
        if not response_1['success']:
            return{
            "success":False, 
            "message": "Could not create unlinked entity", 
            "status" :400
            }
        
        # Delete the original entity documents
        # We are not just changing the index in the document as Dynamo doesn't allow
        # mutation of Primary Keys.
        self.logger.debug('Entity to Delete:'+str(doc))
        response_2 = self.AUM.delete_entity(**doc)


        if not response_2['success']:
            return{
            "success":False, 
            "message": "Could not delete entity", 
            "status" :400
            }
        
        return response_2





    def update_entity(self,type,**kwargs):
        #data = request.json 
           
        result = {}   
        
        #1. Check if the document exists
        response_1 = self.get_entity(type,**kwargs)
        self.logger.debug('Check if doc exists:'+str(type)+':'+str(kwargs))
        self.logger.debug(response_1)

        if response_1['status'] != 200:          

            if response_1['status'] == 404:    
                # It didn't find the user document, create it

                if type == 'user':
                    return self.create_user_funnel(**kwargs)
                else:
                    return{
                        "success":False, 
                        "message": "Nothing to update.", 
                        "status" :400
                    }

                
            else:
                #Something else is wrong, surface the problem. 
                self.logger.debug("Entity does not exist")   
                return response_1
            
        
            
        self.logger.debug("Document found:"+str(response_1['document']))

        #Get the existing document from DB
        entity_doc = response_1['document']

        #Replace values of existing attributes with the ones in the payload
        for key, val in kwargs['payload'].items():
            entity_doc[key] = val

        #Save the document back to DB
        response = self.AUM.update_entity(entity_doc)

        if not response['success']:
            result['success'] = False
            result['message'] = response['message'] 
            result['status'] = response['status']               
            return result
        

        result['success'] = True
        result['message'] = response['message']
        result['document'] = response['document']  
        result['status'] = response['status']   
        #self.logger.debug('Returned object:'+str(result))
        return result



        #MOCK RESULT. Didn't really update the entity
        result['success'] = True
        result['message'] = 'OK'
        result['document'] = {'message':'Ok'}
        result['status'] = 200
        self.logger.debug('Returned object:'+str(result))

        return result




    def create_rel(self,reltype,**data): 
   
        if reltype == 'team:portfolio': #One to Many
            index = 'irn:rel:team:portfolio:' + data['team_id'] + ':*'
            rel = data['portfolio_id']
            
        elif reltype == 'team:user': #One to Many
            index = 'irn:rel:team:user:' + data['team_id'] + ':*'
            rel = data['user_id']

        elif reltype == 'user:team': #One to Many
            index = 'irn:rel:user:team:' + data['user_id'] + ':*'
            rel = data['team_id']

        #elif reltype == 'team/tool:action': #One to Many
        #    index = 'irn:rel:team/tool:action:' + data['team_id'] + '/' + data['tool_id'] + ':*'
        #    rel = data['action_id']
        
        elif reltype == 'team:tool': #One to Many
            index = 'irn:rel:team:tool:' + data['team_id'] + ':*'
            rel = data['tool_id']
                
        elif reltype == 'team/tool:role': #One to Many
            index = 'irn:rel:team/tool:role:' + data['team_id'] + '/' + data['tool_id'] + ':*'
            rel = data['role_id']
             
        elif reltype == 'team/tool:org': #One to Many
            index = 'irn:rel:team/tool:org:' + data['team_id'] + '/' + data['tool_id'] + ':*'
            rel = data['org_id']

        elif reltype == 'team:org': #One to Many
            index = 'irn:rel:team:org:' + data['team_id'] + ':*'
            rel = data['org_id']

        elif reltype == 'email:hash:ttl': #One to Many
            index = 'irn:rel:email:hash:ttl:*:*:*'
            rel = data['email']+':'+data['hash']+':'+str(data['ttl'])

        elif reltype == 'hash:team': #One to Many
            index = 'irn:rel:hash:team:' + data['hash'] + ':*'
            rel = data['team_id']


        rel_document = {
            'index' : index,
            'rel' : rel
        }
            
        self.logger.debug('Rel to Create:'+str(rel_document))
        response = self.AUM.create_rel(**rel_document)

        return response
    


    def delete_rel(self,reltype,**data): 
   
        if reltype == 'team:portfolio': #One to Many
            index = 'irn:rel:team:portfolio:' + data['team_id'] + ':*'
            rel = data['portfolio_id']
            
        elif reltype == 'team:user': #One to Many
            index = 'irn:rel:team:user:' + data['team_id'] + ':*'
            rel = data['user_id']

        elif reltype == 'user:team': #One to Many
            index = 'irn:rel:user:team:' + data['user_id'] + ':*'
            rel = data['team_id']

        #elif reltype == 'team/tool:action': #One to Many
         #   index = 'irn:rel:team/tool:action:' + data['team_id'] + '/' + data['tool_id'] + ':*'
          #  rel = data['action_id']
            
        elif reltype == 'team:tool': #One to Many
            index = 'irn:rel:team:tool:' + data['team_id'] + ':*'
            rel = data['tool_id']
        
        elif reltype == 'team/tool:role': #One to Many
            index = 'irn:rel:team/tool:role:' + data['team_id'] + '/' + data['tool_id'] + ':*'
            rel = data['role_id']
            
        elif reltype == 'team/tool:org': #One to Many
            index = 'irn:rel:team/tool:org:' + data['team_id'] + '/' + data['tool_id'] + ':*'
            rel = data['org_id']
            
            

        elif reltype == 'team:org': #One to Many
            index = 'irn:rel:team:org:' + data['team_id'] + ':*'
            rel = data['org_id']
        
        elif reltype == 'email:hash:ttl': #One to Many
            index = 'irn:rel:email:hash:ttl:*:*:*'
            rel = data['email']+':'+data['hash']+':'+str(data['ttl'])

        elif reltype == 'hash:team': #One to Many
            index = 'irn:rel:hash:team:' + data['hash'] + ':*'
            rel = data['team']


        rel_document = {
            'index' : index,
            'rel' : rel
        }
            
        self.logger.debug('Rel to Create:'+str(rel_document))
        response = self.AUM.delete_rel(**rel_document)

        return response
    


    def get_rel(self,reltype,**data): 
   
        

        if reltype == 'team:portfolio': #One to One
            index = 'irn:rel:team:portfolio:' + data['team_id'] + ':*'
            rel = data['portfolio_id']
            
        elif reltype == 'team:user': #One to Many
            index = 'irn:rel:team:user:' + data['team_id'] + ':*'
            rel = data['user_id']

        elif reltype == 'user:team': #One to Many
            index = 'irn:rel:user:team:' + data['user_id'] + ':*'
            rel = data['team_id']

        #elif reltype == 'team/tool:action': #One to Many
        #    index = 'irn:rel:team/tool:action:' + data['team_id'] + '/' + data['tool_id'] + ':*'
        #    rel = data['action_id']
            
        elif reltype == 'team:tool': #One to Many
            index = 'irn:rel:team:tool:' + data['team_id'] + ':*'
            rel = data['tool_id']
        
        elif reltype == 'team/tool:role': #One to Many
            index = 'irn:rel:team/tool:role:' + data['team_id'] + '/' + data['tool_id'] + ':*'
            rel = data['role_id']
            
        elif reltype == 'team/tool:org': #One to Many
            index = 'irn:rel:team/tool:org:' + data['team_id'] + '/' + data['tool_id'] + ':*'
            rel = data['org_id']
             

        elif reltype == 'team:org': #One to Many
            index = 'irn:rel:team:org:' + data['team_id'] + ':*'
            rel = data['org_id']

        elif reltype == 'email:hash:ttl': #One to Many
            index = 'irn:rel:email:hash:ttl:*:*:*'
            rel = data['email']+':'+data['hash']+':'+str(data['ttl'])

        elif reltype == 'hash:team': #One to Many
            index = 'irn:rel:hash:team:' + data['hash'] + ':*'
            rel = data['team_id']

            
        self.logger.debug('Rel to Get > '+index+rel)
        response = self.AUM.get_rel(index,rel)

        return response
    


    def list_rel(self,reltype,**data): 
             

        if reltype == 'team:portfolio': #One to One
            index = 'irn:rel:team:portfolio:' + data['team_id'] + ':*'
            
            
        elif reltype == 'team:user': #One to Many
            index = 'irn:rel:team:user:' + data['team_id'] + ':*'
            

        elif reltype == 'user:team': #One to Many
            index = 'irn:rel:user:team:' + data['user_id'] + ':*'
            

        #elif reltype == 'team/tool:action': #One to Many
         #   index = 'irn:rel:team/tool:action:' + data['team_id'] + '/' + data['tool_id'] + ':*'
            
        elif reltype == 'team:tool': #One to Many
            index = 'irn:rel:team:tool:' + data['team_id'] + ':*'
            
        elif reltype == 'team/tool:role': #One to Many
            index = 'irn:rel:team/tool:role:' + data['team_id'] + '/' + data['tool_id'] + ':*'
            
        elif reltype == 'team/tool:org': #One to Many
            index = 'irn:rel:team/tool:org:' + data['team_id'] + '/' + data['tool_id'] + ':*'
            

        elif reltype == 'team:org': #One to Many
            index = 'irn:rel:team:org:' + data['team_id'] + ':*'

        elif reltype == 'email:hash:ttl': #One to Many
            index = 'irn:rel:email:hash:ttl:*:*:*'

        elif reltype == 'hash:team': #One to Many
            index = 'irn:rel:hash:team:' + data['hash'] + ':*'
            
 
        self.logger.debug('List Rels > '+index)
        response = self.AUM.list_rel(index)

        return response


    #--------------------------------------------------SPECIALIZED FUNCTIONS


    def get_team_users(self,**kwargs):

        userdict = {}

        #1. Retrieve rel >  team:user:
        if 'team_id' in kwargs:
            index = 'irn:rel:team:user:' + kwargs['team_id'] + ':*'
            response_1 = self.AUM.list_rel(index)
            
            #3. Call the entity document of each user to retrieve the email 
            # and name from team members. If there are more than 10 users, 
            # just output the first ten.
            self.logger.debug(f'List of team users:{response_1["document"]["items"]}',)

            for item in response_1['document']['items']:

                type = 'user' 
                response_2 = self.get_entity(type,user_id=item['rel'])
                
                if not response_2['success']:
                    self.logger.debug(f'Could not find entity of user:{item["rel"]}')
                    continue

                team_user_doc = response_2['document']
                self.logger.debug('Team User Document:'+str(team_user_doc))

                userdict[team_user_doc['_id']] = {}
                userdict[team_user_doc['_id']]['user_id'] = team_user_doc['_id']
                userdict[team_user_doc['_id']]['name'] = team_user_doc['name']
                userdict[team_user_doc['_id']]['last'] = team_user_doc['slot_a']
                userdict[team_user_doc['_id']]['email'] = team_user_doc['email']


            result = {}
            #2. Check whether the requesting user is member of the team.
            if kwargs['user_id'] not in userdict:
                result['success'] = False
                result['message'] = 'User is not in this team'
                result['status'] = 400              
                return result
            

            result['success'] = True
            result['message'] = 'Users in the team'
            result['document'] = userdict 
            result['status'] = 200

            return result



    def assign_team_orgs(self,**kwargs):

        result = {}
        #Check minimum requirements:
        required_keys = ['user_id','team_id','org_id'] 
        if not all(key in kwargs for key in required_keys):
            result['success'] = False
            result['message'] = 'Missing attributes' 
            result['status'] = 400             
            return result

        #1. Check if user_id making the assignment, the team_id and org_id belong to the same portfolio 
        # Only users that belong to the portfolio should be able to assign orgs to teams. 
        # Only teams and orgs under the same portfolio should be linked
        
        check = self.is_user_team_org_same_portfolio(
            user_id=kwargs['user_id'],
            team_id=kwargs['team_id'],
            org_id=kwargs['org_id']
            )
        
        if not check:
            result['success'] = False
            result['message'] = 'Org does not belong to portfolio' 
            result['status'] = 400             
            return result

        # Checking if user_id belongs to a team that is allowed to access the 
        # route that runs this function is responsibility of auth_check 
        
        
        #2. Create rel
        reltype = 'team:org'
        if kwargs['method'] == 'POST':
            response = self.create_rel(
                reltype,
                team_id=kwargs['team_id'],
                org_id=kwargs['org_id']
                )
        elif kwargs['method'] == 'DELETE':
            response = self.delete_rel(
                reltype,
                team_id=kwargs['team_id'],
                org_id=kwargs['org_id']
                )
            
        return response

        

    def generate_handle(self,name):
        # Use regex to find only capital letters (A-Z) or numbers (0-9)
        handle = re.sub(r'[^A-Z0-9]', '', name)
        
        # Return the first 10 characters
        return handle[:10]



    def generate_numeric_hash(self,input_string,length=20):
        # Create a SHA-256 hash object
        sha256_hash = hashlib.sha256()

        # Update the hash object with the input string encoded as bytes
        sha256_hash.update(input_string.encode('utf-8'))

        # Get the hexadecimal digest of the hash
        hex_hash = sha256_hash.hexdigest()

        # Convert the hexadecimal hash to an integer
        numeric_hash = int(hex_hash, 16)

        # Optionally, truncate the number to a specific length
        numeric_hash_str = str(numeric_hash)[:length]  # Adjust the length as needed

        return numeric_hash_str


    
    def generate_invite_hash(self,email,ttl):
        secret_key = str(self.config.get('SECRET_KEY') or '').strip()
        if not secret_key:
            secret_key = globals().get('SECRET_KEY', '')
        string_to_hash = email + secret_key + str(ttl)
        return self.generate_numeric_hash(string_to_hash,6)

    def _split_invitee_name(self, full_name, email):
        """Split a full name into Cognito given_name / family_name parts."""
        parts = full_name.strip().split() if full_name else []
        if len(parts) >= 2:
            return parts[0], ' '.join(parts[1:])
        if parts:
            return parts[0], ''
        local_part = (email or '').split('@')[0] or 'User'
        return local_part, ''

    def _lookup_invitee_name(self, email, team_id):
        """Resolve invitee display name from pending noma_attendant records."""
        from renglo.data.data_controller import DataController

        portfolio_id = None
        team_portfolio = self.list_rel('team:portfolio', team_id=team_id)
        items = (team_portfolio.get('document') or {}).get('items') or []
        if team_portfolio.get('success') and items:
            portfolio_id = items[0].get('rel')

        if not portfolio_id:
            return None

        dac = DataController(config=self.config)
        org_resp = self.list_rel('team:org', team_id=team_id)
        org_items = (org_resp.get('document') or {}).get('items') or []
        org_ids = [item.get('rel') for item in org_items if item.get('rel')]

        normalized_email = email.lower().strip()
        for org_id in org_ids:
            try:
                resp = dac.get_a_b(portfolio_id, org_id, 'noma_attendants', limit=1000)
                for attendant in (resp.get('items') or []):
                    attendant_email = (attendant.get('email') or '').lower().strip()
                    if attendant_email == normalized_email:
                        name = (attendant.get('name') or '').strip()
                        if name:
                            return name
            except Exception as exc:
                self.logger.debug('Invite name lookup failed for org %s: %s', org_id, exc)
                continue

        return None

    def _validate_invite_code(self, email, code):
        """Validate invite email/code pair. Returns success dict with hash and team_id."""
        index = 'irn:rel:email:hash:ttl:*:*:*'
        prefix = email + ':' + code
        response = self.AUM.list_rel_prefix(index, prefix)

        if not (response.get('success') and response.get('document')):
            return {
                'success': False,
                'message': 'Invitation is invalid ',
                'status': 404,
            }

        documents = response['document']
        if not documents:
            return {
                'success': False,
                'message': 'Invitation is invalid ',
                'status': 404,
            }

        rel_email, rel_hash, ttl = documents[0]['rel'].split(':')
        if str(self.generate_ttl(0)) > str(ttl):
            return {
                'success': False,
                'message': 'Invitation is expired',
                'status': 410,
            }

        if code != self.generate_invite_hash(rel_email, ttl):
            return {
                'success': False,
                'message': 'Invitation is invalid',
                'status': 400,
            }

        team_id = None
        team_rel = self.list_rel('hash:team', hash=rel_hash)
        team_items = (team_rel.get('document') or {}).get('items') or []
        if team_rel.get('success') and team_items:
            team_id = team_items[0].get('rel')

        invitee_name = self._lookup_invitee_name(email, team_id) if team_id else None
        first, last = self._split_invitee_name(invitee_name or '', email)

        return {
            'success': True,
            'message': 'Invitation is valid',
            'status': 200,
            'document': {
                'email': rel_email,
                'hash': rel_hash,
                'team_id': team_id,
                'name': invitee_name or '',
                'first': first,
                'last': last,
            },
        }

    def get_invite_preview(self, email, code):
        """Public invite preview for the accept-invite page (no auth required)."""
        if not email or not code:
            return {
                'success': False,
                'message': 'Missing attributes',
                'status': 400,
            }
        return self._validate_invite_code(email.strip(), code.strip())
    


    # Function to generate TTL timestamp 24 hours from now
    def generate_ttl(self,offset_min=0):
        current_time = int(time.time())  # Current time in UNIX timestamp (seconds)
        ttl = current_time + offset_min * 60  # TTL (in seconds)
        return ttl




        
        


    #--------------------------------------------------ENTITY CREATION FUNNELS


    def create_user_funnel(self,**kwargs):
        result = {}
        transaction = []

        self.logger.debug('Initiating CREATE USER FUNNEL')

        #1. Create User Document
        response_1 = self.create_entity('user',**kwargs)
        self.logger.debug('Step 1: Create User Document')
        if not response_1['success']: 
                               
            return response_1
        else:
            transaction.append(response_1)

        
 
        #All went good, Summarize Transaction Success             
        result['success'] = True
        result['message'] = 'Create User Funnel completed'
        result['status'] = 200 
        result['document'] = transaction  

        self.logger.debug(result)
        return result
    




    def create_portfolio_funnel(self,**kwargs):
        result = {}
        transaction = []

        self.logger.debug('Initiating CREATE PORTFOLIO FUNNEL')


        #1. Create Porfolio Document
        #Input: kwargs['name'] and kwargs['about'] 
        response_1 = self.create_entity('portfolio',**kwargs)
        
        self.logger.debug('Step 1: Creating Porfolio Document')
        self.logger.debug(response_1)
        
  
        if not response_1['success']:
            detail = (response_1.get('message') or '').strip()
            base = 'Could not create the Portfolio Entity'
            response_1['message'] = f"{base}: {detail}" if detail else base
            return response_1
        else:
            transaction.append(response_1)
        

        
        self.logger.debug('Step 2: SKIPPED')

        

        #3. Create a default Team
        kwargs['name'] = 'Admin'
        kwargs['about'] = 'This team enables its users to change portfolio settings.'
        kwargs['portfolio_id'] = response_1['document']['_id'] #This is the portfolio_id
        response_3 = self.create_entity('team',**kwargs)

        self.logger.debug('Step 3: Creating a default Team')
        self.logger.debug(response_3)
        # Team documents explicitly relate to Portfolios. That's why you don't need a Portfolio:Teams rel.

        if not response_3['success']:
            response_3['message'] = 'Could not create Team'                
            return response_3 
        else:
            transaction.append(response_3)


        #3b. Create a Team to Portfolio Rel
        rel_data = {}
        rel_data['portfolio_id'] = response_1['document']['_id'] #This is the portfolio_id
        rel_data['team_id'] = response_3['document']['_id'] #This is the team_id
        response_3b = self.create_rel('team:portfolio',**rel_data)

        self.logger.debug('Step 3b:Creating Team-Portfolio relationship')
        self.logger.debug(response_3b)

        if not response_3b['success']:
            response_3b['message'] = 'Could not create Team-Portfolio relationship'                
            return response_3b
        else:
            transaction.append(response_3b)


          

        #4. Create Team to User Rel
        rel_data = {}
        rel_data['user_id'] = kwargs['user_id']
        rel_data['team_id'] = response_3['document']['_id'] #This is the team_id
        response_4 = self.create_rel('team:user',**rel_data)

        self.logger.debug('Step 4:Creating Team-User relationship')
        self.logger.debug(response_4)

        if not response_4['success']:
            response_4['message'] = 'Could not create Team-User relationship'                
            return response_4
        else:
            transaction.append(response_4)

        

        #5. Create User to Team Rel (Adding the owner as the first member of the team)
        rel_data = {}
        rel_data['user_id'] = kwargs['user_id']
        rel_data['team_id'] = response_3['document']['_id'] #This is the team_id
        response_5 = self.create_rel('user:team',**rel_data)

        self.logger.debug('Step 5: Creating User to Team relationship ')
        self.logger.debug(response_5)

        if not response_5['success']:
            response_5['message'] = 'Could not create User-Team relationship'                
            return response_5
        else:
            transaction.append(response_5)


        
        
        '''
        #5b. Create a Tool instance entity
        kwargs['name'] = 'Auth'
        kwargs['handle'] = '_auth'
        kwargs['about'] = 'This app allows you to create entities.'
        kwargs['portfolio_id'] = response_1['document']['_id'] #This is the portfolio_id
        response_5b = self.create_entity('tool',**kwargs)

        self.logger.debug('Step 5b: Installing default tool in portfolio')
        self.logger.debug(response_5b)
        # Team documents explicitly relate to Portfolios. That's why you don't need a Portfolio:Teams rel.

        if not response_5b['success']:
            response_5b['message'] = 'Could not install Tool'                
            return response_5b 
        else:
            transaction.append(response_5b)

        
        #CHANGE ACTIONS FOR ROLES.  ROLES ARE MORE GENERAL. 
        #ROLES WILL BE DETERMINED BY THE APP DESIGNER
        #THERE WILL BE NO CUSTOM ROLES
        #EVERY API ENDPOINT WILL AUTHORIZE BASED ON WHAT ACTIONS A ROLE IS ALLOWED TO EXECUTE
        #THERE IS A ROLE-ACTION OBJECT IN THE SOURCE CODE
        #IF A ROLE NEEDS TO ACQUIRE/LOSE ACTIONS, IT WILL BE DONE VIA CODE UPDATE
        #ROLES WILL BE WELL THOUGHT TO COVER ALL ACCESS PATTERNS TO AVOID HAVING TO CREATE CUSTOM ROLES
        #CUSTOM ROLES ARE AN ANTIPATTERN. SAME AS YOU DONT CREATE CUSTOM ROLES IN BASEBALL BUT USE WHAT EXISTS 
        #IN ORDER TO CREATE A NEW ROLE YOU NEED TO HAVE DEEP UNDERSTANDING OF WHAT THE TOOL DOES. 
        
        
        #- A portfolio has a tool (Defined as an ENTITY owned by the portfolio)
        #- A tool has x Roles (Roles are hardcoded to the Tool)
        #- A tool+role combination is assigned to a team (Defined as a REL that links the Team with a Tool )
        #- A role has a list of actions
        
        

        #6. Create Team/Tool to Role Rel
        rel_data = {}
        rel_data['team_id'] = response_3['document']['_id'] #This is the default team_id
        rel_data['tool_id'] = response_5b['document']['_id'] #This is the default tool_id
        rel_data['role_id'] = 'DataEntry'
        response_6 = self.create_rel('team/tool:role',**rel_data)

        self.logger.debug('Step 6:Create Team/Tool to Role : DataEntry')
        self.logger.debug(response_6)

        if not response_6['success']:
            response_6['message'] = 'Could not create Team/Tool-action relationship'                
            return response_6
        else:
            transaction.append(response_6)


        #6b. Create Team/Tool to Role Rel (2)
        rel_data = {}
        rel_data['team_id'] = response_3['document']['_id'] #This is the default team_id
        rel_data['tool_id'] = response_5b['document']['_id'] #This is the default tool_id
        rel_data['role_id'] = 'Moderator'
        response_6b = self.create_rel('team/tool:role',**rel_data)

        self.logger.debug('Step 6b:Create Team/Tool to Role : Moderator')
        self.logger.debug(response_6b)

        if not response_6b['success']:
            response_6b['message'] = 'Could not create Team/Tool-role relationship'                
            return response_6b
        else:
            transaction.append(response_6b)
            
            
            
            
            
            
        # We can't create the tool to Org relationship as no orgs exist yet. We'll do that in a later step.

        #7. Create a default Org
        kwargs['name'] = 'First Org'
        kwargs['handle'] = '1f'
        kwargs['about'] = 'This is your first org'
        kwargs['portfolio_id'] = response_1['document']['_id'] #This is the portfolio_id
        response_7 = self.create_entity('org',**kwargs)

        self.logger.debug('Step 7: Creating new org')
        self.logger.debug(response_7)
        # Org documents explicitly relate to Portfolios. That's why you don't need a Portfolio:Orgs rel.

        if not response_7['success']:
            response_7['message'] = 'Could not install App'                
            return response_7 
        else:
            transaction.append(response_7)


        #8. Create Team to Org rel
        rel_data = {}
        rel_data['team_id'] = response_3['document']['_id'] #This is the team_id
        rel_data['org_id'] = response_7['document']['_id'] #This is the org_id
        rel_data['action_id'] = 'useApp'
        response_8 = self.create_rel('team:org',**rel_data)

        self.logger.debug('Step 8:Create team to org rel')
        self.logger.debug(response_8)

        if not response_8['success']:
            response_8['message'] = 'Could not create Team-Org relationship'                
            return response_8
        else:
            transaction.append(response_8)
            
            
        
        #9. Create Team/Tool to Org Rel
        rel_data = {}
        rel_data['team_id'] = response_3['document']['_id'] #This is the default team_id
        rel_data['tool_id'] = response_5b['document']['_id'] #This is the default tool_id
        rel_data['org_id'] = response_7['document']['_id'] #This is the default org_id
        response_9 = self.create_rel('team/tool:org',**rel_data)

        self.logger.debug('Step 9:Create Team/Tool to Role : DataEntry')
        self.logger.debug(response_9)

        if not response_9['success']:
            response_9['message'] = 'Could not create Team/Tool-action relationship'                
            return response_9
        else:
            transaction.append(response_9)



        '''
        
        #All went good, Summarize Transaction Success 
        self.logger.debug('End of Funnel ')

        result['success'] = True
        result['message'] = 'Create Portfolio Funnel completed, Ok'
        result['status'] = 200 
        result['document'] = transaction  

        self.logger.debug(result)            
        return result
    

        



    
    def create_org_funnel(self,**kwargs):
        result = {}
        transaction = []

        self.logger.debug('Initiating CREATE ORG FUNNEL')


        #1. Create a new Org

        #Check minimum requirements:
        required_keys = ['name','portfolio_id'] 
        if not all(key in kwargs for key in required_keys):
            response_0 = {}
            response_0['success'] = False
            response_0['message'] = 'Missing attributes' 
            response_0['status'] = 400             
            return response_0

        kwargs['handle'] = self.generate_handle(kwargs['name'])
        response_1 = self.create_entity('org',**kwargs)

        self.logger.debug('Step 1: Creating new org')
        self.logger.debug(response_1)
        # Org documents explicitly relate to Portfolios. That's why we don't need a Portfolio:Orgs rel.

        if not response_1['success']:
            response_1['message'] = 'Could not create Org'                
            return response_1 
        else:
            transaction.append(response_1)

        # Auto-install default tools/workflows for the new org.
        # This ensures the org has schd_tools, schd_actions, jobs, and
        # noma_config immediately—no manual Console upload needed.
        onboarding_ok = False
        onboarding_skipped = False
        try:
            from noma.handlers.noma_onboardings import NomaOnboardings  # type: ignore

            portfolio_id = kwargs.get("portfolio_id")
            user_id = kwargs.get("user_id") or self.get_current_user()
            org_id = response_1.get("document", {}).get("_id")

            team_id = kwargs.get("team_id")
            if not team_id and portfolio_id and user_id:
                team_id = self._pick_user_team_in_portfolio(
                    user_id=user_id, portfolio_id=portfolio_id
                )
                if not team_id:
                    # Portfolios are expected to always have a team (normally
                    # bootstrapped by create_portfolio_funnel), but some
                    # (e.g. accessed by a global admin, or created before
                    # that invariant existed) don't. Rather than failing org
                    # provisioning because of it, bootstrap a default team.
                    team_id = self._ensure_default_team_for_portfolio(
                        portfolio_id=portfolio_id, user_id=user_id
                    )

            if portfolio_id and org_id and team_id:
                onboarding = NomaOnboardings()
                onboarding_payload = {
                    "portfolio": portfolio_id,
                    "team": team_id,
                    "org": org_id,
                    "name": kwargs.get("name", "org"),
                }
                onboarding_result = onboarding.run(onboarding_payload)
                onboarding_ok = bool(onboarding_result.get("success"))
                transaction.append(
                    {
                        "success": onboarding_ok,
                        "message": "Installed default tools for org"
                        if onboarding_ok
                        else "Failed installing default tools for org",
                        "document": onboarding_result,
                        "status": 200 if onboarding_ok else 500,
                        "action": "install_default_tools",
                    }
                )
                self.logger.info(
                    "create_org_funnel | install_default_tools "
                    f"| portfolio={portfolio_id} org={org_id} team={team_id} "
                    f"| success={onboarding_ok}"
                )
            else:
                onboarding_skipped = True
                self.logger.warning(
                    "create_org_funnel | install_default_tools SKIPPED "
                    f"| portfolio={portfolio_id} org={org_id} team={team_id} "
                    "| reason=missing_ids"
                )
                transaction.append(
                    {
                        "success": False,
                        "message": "Skipped tool install: could not resolve team for portfolio",
                        "status": 500,
                        "action": "install_default_tools",
                    }
                )
        except Exception as e:
            self.logger.exception(
                f"create_org_funnel | install_default_tools EXCEPTION | {e}"
            )
            transaction.append(
                {
                    "success": False,
                    "message": f"Tool install exception: {e}",
                    "status": 500,
                    "action": "install_default_tools",
                }
            )

        self.logger.debug('End of Funnel ')

        if onboarding_ok:
            result['success'] = True
            result['message'] = 'Create Org Funnel completed, Ok'
            result['status'] = 200
        else:
            result['success'] = True
            result['message'] = 'Org created but tool provisioning failed'
            result['status'] = 207
            result['provisioning_failed'] = True

        result['document'] = transaction

        self.logger.debug(result)
        return result

    def _ensure_default_team_for_portfolio(self, portfolio_id: str, user_id: str):
        """
        Best-effort bootstrap of a default team for portfolios that somehow
        have none resolvable for this user (e.g. accessed by a global admin,
        or a portfolio created outside create_portfolio_funnel). Mirrors the
        'Admin' team create_portfolio_funnel normally creates, so
        create_org_funnel doesn't have to skip tool provisioning for lack of
        a team to hang the org off of.
        """
        if not portfolio_id or not user_id:
            return None

        try:
            team_kwargs = {
                'name': 'Admin',
                'about': 'This team enables its users to change portfolio settings.',
                'portfolio_id': portfolio_id,
                'user_id': user_id,
            }
            response_team = self.create_entity('team', **team_kwargs)
            if not response_team.get('success'):
                self.logger.warning(
                    f"_ensure_default_team_for_portfolio: could not create team | {response_team}"
                )
                return None

            team_id = response_team['document']['_id']

            response_rel_1 = self.create_rel(
                'team:portfolio', team_id=team_id, portfolio_id=portfolio_id
            )
            if not response_rel_1.get('success'):
                self.logger.warning(
                    f"_ensure_default_team_for_portfolio: could not link team:portfolio | {response_rel_1}"
                )
                return None

            self.create_rel('team:user', team_id=team_id, user_id=user_id)
            self.create_rel('user:team', team_id=team_id, user_id=user_id)

            self.logger.info(
                "_ensure_default_team_for_portfolio: bootstrapped team "
                f"{team_id} for portfolio {portfolio_id}"
            )
            return team_id
        except Exception:
            self.logger.exception("_ensure_default_team_for_portfolio failed")
            return None

    def _pick_user_team_in_portfolio(self, user_id: str, portfolio_id: str):
        """
        Best-effort selection of a team id for a given user within a portfolio.
        Used to wire up default tools/relations when a client creates a new org
        without specifying a team.

        Strategy:
        1. Walk user->team rels and find one linked to this portfolio.
        2. Fallback: query portfolio's team entities and pick the first one
           (typically the 'Admin' team created by create_portfolio_funnel).
        """
        try:
            index = 'irn:rel:user:team:' + user_id + ':*'
            rels_user_teams = self.AUM.list_rel(index)
            items = (rels_user_teams or {}).get("document", {}).get("items", []) or []
            for item in items:
                team_id = item.get("rel")
                if not team_id:
                    continue
                rel = self.get_rel(
                    "team:portfolio", team_id=team_id, portfolio_id=portfolio_id
                )
                if rel and rel.get("success"):
                    return team_id
        except Exception:
            self.logger.exception("Failed picking user team for portfolio via rels")

        # Fallback: query team entities owned by this portfolio directly.
        try:
            index = f'irn:entity:portfolio/team:{portfolio_id}:*'
            teams_resp = self.AUM.list_entity(index, limit=50)
            team_items = (teams_resp or {}).get("document", {}).get("items", []) or []
            for team in team_items:
                tid = team.get("_id")
                if tid:
                    self.logger.debug(
                        f"_pick_user_team_in_portfolio fallback: using team {tid} "
                        f"(name={team.get('name')}) from portfolio entity list"
                    )
                    return tid
        except Exception:
            self.logger.exception("Failed picking team via portfolio entity fallback")

        return None



    def create_team_funnel(self,**kwargs):
        result = {}
        transaction = []

        self.logger.debug('Initiating CREATE TEAM FUNNEL')

        #1. Create a new Team
        required_keys = ['name','portfolio_id'] 
        #Check minimum requirements:
        if not all(key in kwargs for key in required_keys):
            response_0 = {}
            response_0['success'] = False
            response_0['message'] = 'Missing attributes' 
            response_0['status'] = 400             
            return response_0
        
        response_1 = self.create_entity('team',**kwargs)
        self.logger.debug('Step 1: Creating a new Team')
        self.logger.debug(response_1)
        # Team documents explicitly relate to Portfolios. That's why you don't need a Portfolio:Teams rel.

        if not response_1['success']:
            response_1['message'] = 'Could not create Team'                
            return response_1 
        else:
            transaction.append(response_1)



        #2. Create a Team to Portfolio Rel (You need this rel to assemble the Auth tree)
        rel_data = {}
        rel_data['portfolio_id'] = kwargs['portfolio_id'] #This is the portfolio_id
        rel_data['team_id'] = response_1['document']['_id'] #This is the team_id
        response_2 = self.create_rel('team:portfolio',**rel_data)

        self.logger.debug('Step 2:Creating Team-Portfolio relationship')
        self.logger.debug(response_2)

        if not response_2['success']:
            response_2['message'] = 'Could not create Team-Portfolio relationship'                
            return response_2
        else:
            transaction.append(response_2)




        #3. Create Team to User Rel
        rel_data = {}
        rel_data['user_id'] = kwargs['user_id']
        rel_data['team_id'] = response_1['document']['_id'] #This is the team_id
        response_3 = self.create_rel('team:user',**rel_data)

        self.logger.debug('Step 3:Creating Team-User relationship')
        self.logger.debug(response_3)

        if not response_3['success']:
            response_3['message'] = 'Could not create Team-User relationship'                
            return response_3
        else:
            transaction.append(response_3)

        

        #4. Create User to Team Rel (Adding the owner as the first member of the team)
        rel_data = {}
        rel_data['user_id'] = kwargs['user_id']
        rel_data['team_id'] = response_1['document']['_id'] #This is the team_id
        response_4 = self.create_rel('user:team',**rel_data)

        self.logger.debug('Step 4: Creating User to Team relationship ')
        self.logger.debug(response_4)

        if not response_4['success']:
            response_4['message'] = 'Could not create User-Team relationship'                
            return response_4
        else:
            transaction.append(response_4)





        #All went good, Summarize Transaction Success 
        self.logger.debug('End of Funnel ')

        result['success'] = True
        result['message'] = 'Create Team Funnel completed, Ok'
        result['status'] = 200 
        result['document'] = transaction  

        self.logger.debug(result)            
        return result
    


    def add_user_to_team_funnel(self,**kwargs):
        result = {}
        transaction = []

        self.logger.debug('Initiating ADD USER TO TEAM FUNNEL')

        #0. Check for requirements
        required_keys = ['user_id','team_id'] 
        #Check minimum requirements:
        if not all(key in kwargs for key in required_keys):
            response_0 = {}
            response_0['success'] = False
            response_0['message'] = 'Missing attributes' 
            response_0['status'] = 400             
            return response_0
         

        #1. Create Team to User Rel
        rel_data = {}
        rel_data['user_id'] = kwargs['user_id']
        rel_data['team_id'] = kwargs['team_id'] #This is the team_id
        response_1 = self.create_rel('team:user',**rel_data)

        self.logger.debug('Step 1:Creating Team-User relationship')
        self.logger.debug(response_1)

        if not response_1['success']:
            response_1['message'] = 'Could not create Team-User relationship'                
            return response_1
        else:
            transaction.append(response_1)

        

        #2. Create User to Team Rel (Adding the owner as the first member of the team)
        rel_data = {}
        rel_data['user_id'] = kwargs['user_id']
        rel_data['team_id'] = kwargs['team_id'] #This is the team_id
        response_2 = self.create_rel('user:team',**rel_data)

        self.logger.debug('Step 2: Creating User to Team relationship ')
        self.logger.debug(response_2)

        if not response_2['success']:
            response_2['message'] = 'Could not create User-Team relationship'                
            return response_2
        else:
            transaction.append(response_2)



        #All went good, Summarize Transaction Success 
        self.logger.debug('End of Funnel ')

        result['success'] = True
        result['message'] = 'Add user to Team Funnel completed, Ok'
        result['status'] = 200 
        result['document'] = transaction  

        self.logger.debug(result)            
        return result
    


    # ------------------------------------------------------------------
    # Invite email rendering (Noma brand)
    # ------------------------------------------------------------------
    # Cores e tipografia espelham o template do frontend
    # (Noma/app/api/email/send-flights/route.ts) para manter consistência
    # visual entre e-mails transacionais.
    _INVITE_BRAND = {
        'navy': '#0B1724',
        'green': '#6CEF92',
        'green_light': '#E8FEF0',
        'bg': '#f5f3ee',
        'body_text': '#374151',
        'muted': '#9ca3af',
        'card_border': '#e8f5e0',
    }

    # i18n strings for the invite email. Keep keys stable; only translations change.
    # Placeholders use Python str.format syntax: {sender}, {team}, {brand}.
    _INVITE_I18N = {
        'en': {
            'lang_attr': 'en',
            'title': '{brand} invitation',
            'header_subtitle': 'You have a new invitation',
            'welcome': 'Welcome to {brand} 👋',
            'invited_by_html_with_sender': '{sender} invited you to join <strong>{team}</strong> on {brand}.',
            'invited_by_html_no_sender': "You've been invited to join <strong>{team}</strong> on {brand}.",
            'invited_by_text_with_sender': '{sender} invited you to join {team} on {brand}.',
            'invited_by_text_no_sender': "You've been invited to join {team} on {brand}.",
            'subject_with_sender': '{sender} invited you to {team} on {brand}',
            'subject_no_sender': "You're invited to join {team} on {brand}",
            'cta': 'Accept invitation →',
            'cta_sign_in': 'Sign in →',
            'fallback_intro': "If the button doesn't work, paste this link in your browser:",
            'expiry_hint': "This invitation expires in 24 hours. If you weren't expecting this email, you can safely ignore it.",
            'expiry_hint_existing': "If you weren't expecting this email, you can safely ignore it.",
            'footer': 'Sent by',
            'accept_action_text': 'Accept the invitation:',
            'sign_in_action_text': 'Sign in to continue:',
            'fallback_team_label': 'your team',
            'signature_dash': '— ',
        },
        'pt': {
            'lang_attr': 'pt-BR',
            'title': 'Convite para {brand}',
            'header_subtitle': 'Você tem um novo convite',
            'welcome': 'Bem-vindo(a) ao {brand} 👋',
            'invited_by_html_with_sender': '{sender} convidou você para entrar em <strong>{team}</strong> no {brand}.',
            'invited_by_html_no_sender': 'Você foi convidado(a) para entrar em <strong>{team}</strong> no {brand}.',
            'invited_by_text_with_sender': '{sender} convidou você para entrar em {team} no {brand}.',
            'invited_by_text_no_sender': 'Você foi convidado(a) para entrar em {team} no {brand}.',
            'subject_with_sender': '{sender} convidou você para {team} no {brand}',
            'subject_no_sender': 'Você foi convidado(a) para entrar em {team} no {brand}',
            'cta': 'Aceitar convite →',
            'cta_sign_in': 'Entrar →',
            'fallback_intro': 'Se o botão não funcionar, cole este link no seu navegador:',
            'expiry_hint': 'Este convite expira em 24 horas. Se você não estava esperando este e-mail, pode ignorá-lo com segurança.',
            'expiry_hint_existing': 'Se você não estava esperando este e-mail, pode ignorá-lo com segurança.',
            'footer': 'Enviado por',
            'accept_action_text': 'Aceitar o convite:',
            'sign_in_action_text': 'Entre para continuar:',
            'fallback_team_label': 'seu time',
            'signature_dash': '— ',
        },
    }

    def _build_invite_email(self, sender_name, portfolio_name, team_name,
                            code, invite_url, brand_name='Noma', locale='en',
                            existing_user=False):
        """
        Renders the invite email in Noma brand style, localized per `locale`.
        Returns dict with keys: subject, body_text, body_html.

        sender_name pode vir vazio (quando o doc do convidador não tem nome);
        nesse caso usamos um fraseamento neutro para não exibir "by  to team".

        locale: 'pt' or 'en' (defaults to 'en' if unsupported value provided).
        The `code` arg is kept in the signature for backward compatibility,
        but is no longer shown in the email body (the invite URL already
        carries it).

        existing_user: when True, CTA points at sign-in (account already exists)
        instead of the /invite accept flow.
        """
        brand = self._INVITE_BRAND
        # Normalize locale: accept variants like 'pt-BR', 'en-US'.
        loc_root = (locale or 'en').split('-')[0].lower()
        t = self._INVITE_I18N.get(loc_root) or self._INVITE_I18N['en']

        team_label = portfolio_name or team_name or t['fallback_team_label']
        sender_display = (sender_name or '').strip()

        invited_by_line = (
            t['invited_by_html_with_sender'].format(sender=sender_display, team=team_label, brand=brand_name)
            if sender_display
            else t['invited_by_html_no_sender'].format(team=team_label, brand=brand_name)
        )
        invited_by_text = (
            t['invited_by_text_with_sender'].format(sender=sender_display, team=team_label, brand=brand_name)
            if sender_display
            else t['invited_by_text_no_sender'].format(team=team_label, brand=brand_name)
        )

        subject = (
            t['subject_with_sender'].format(sender=sender_display, team=team_label, brand=brand_name)
            if sender_display
            else t['subject_no_sender'].format(team=team_label, brand=brand_name)
        )

        cta_label = t['cta_sign_in'] if existing_user else t['cta']
        action_text = t['sign_in_action_text'] if existing_user else t['accept_action_text']
        expiry_hint = t['expiry_hint_existing'] if existing_user else t['expiry_hint']

        body_text = (
            f"{invited_by_text}\n\n"
            f"{action_text}\n{invite_url}\n\n"
            f"{expiry_hint}\n\n"
            f"{t['signature_dash']}{brand_name}"
        )

        body_html = (
            '<!DOCTYPE html>'
            f'<html lang="{t["lang_attr"]}"><head>'
            '<meta charset="UTF-8" />'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0" />'
            f'<title>{t["title"].format(brand=brand_name)}</title>'
            '</head>'
            f'<body style="margin:0;padding:0;background:{brand["bg"]};'
            "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;\">"
            f'<table cellpadding="0" cellspacing="0" width="100%" style="background:{brand["bg"]};">'
            '<tr><td align="center" style="padding:32px 16px;">'
            '<table cellpadding="0" cellspacing="0" width="100%" style="max-width:560px;">'

            # Header
            '<tr><td style="background:' + brand['navy'] + ';border-radius:16px 16px 0 0;padding:28px 32px;">'
            '<div style="font-size:24px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;">'
            f'<span style="color:{brand["green"]};">✦</span> {brand_name}'
            '</div>'
            '<div style="font-size:13px;color:rgba(255,255,255,0.65);margin-top:4px;">'
            f'{t["header_subtitle"]}'
            '</div>'
            '</td></tr>'

            # Body
            f'<tr><td style="background:#ffffff;padding:32px 32px 16px;">'
            f'<h1 style="margin:0 0 8px;font-size:22px;font-weight:800;color:{brand["navy"]};letter-spacing:-0.4px;">'
            f'{t["welcome"].format(brand=brand_name)}'
            '</h1>'
            f'<p style="margin:0 0 24px;font-size:15px;line-height:1.6;color:{brand["body_text"]};">'
            f'{invited_by_line}'
            '</p>'

            # CTA button
            '<table cellpadding="0" cellspacing="0" width="100%"><tr><td align="center" style="padding:8px 0 24px;">'
            f'<a href="{invite_url}" target="_blank" '
            f'style="display:inline-block;padding:14px 32px;background:{brand["navy"]};'
            'color:#ffffff;font-size:14px;font-weight:700;text-decoration:none;'
            'border-radius:10px;letter-spacing:0.2px;">'
            f'{cta_label}'
            '</a>'
            '</td></tr></table>'

            # Fallback link
            f'<p style="margin:0 0 8px;font-size:12px;color:{brand["muted"]};">'
            f'{t["fallback_intro"]}'
            '</p>'
            f'<p style="margin:0 0 24px;font-size:12px;color:{brand["body_text"]};word-break:break-all;">'
            f'<a href="{invite_url}" style="color:{brand["navy"]};text-decoration:underline;">{invite_url}</a>'
            '</p>'

            # Expiry hint
            f'<p style="margin:0;font-size:12px;color:{brand["muted"]};line-height:1.5;">'
            f'{expiry_hint}'
            '</p>'
            '</td></tr>'

            # Footer
            f'<tr><td style="background:#f9fdf9;border-top:1px solid {brand["green_light"]};'
            'border-radius:0 0 16px 16px;padding:20px 28px;">'
            f'<p style="margin:0;font-size:12px;color:{brand["muted"]};text-align:center;">'
            f'{t["footer"]} <strong style="color:{brand["navy"]};">{brand_name}</strong>'
            '</p>'
            '</td></tr>'

            '</table></td></tr></table></body></html>'
        )

        return {
            'subject': subject,
            'body_text': body_text,
            'body_html': body_html,
        }


    def invite_user_funnel(self,**kwargs):
        bridge = {}
        result = {}
        transaction = []

        self.logger.debug('Initiating INVITE USER FUNNEL')


        #1. Check minimum requirements

        required_keys = ['email','team_id'] 
        if not all(key in kwargs for key in required_keys):
            response_0 = {}
            response_0['success'] = False
            response_0['message'] = 'Missing attributes' 
            response_0['status'] = 400             
            return response_0
        


        #1a. Get Team document
        response_1a = self.get_entity(
            'team',
            portfolio_id=kwargs['portfolio_id'],
            team_id=kwargs['team_id']
        )
        if not response_1a['success']:
            return response_1a
        else:
            bridge['teamdoc'] = response_1a['document']


        #1b. Get Sender document (optional — email copy works without a name)
        response_1b = self.get_entity(
            'user',
            user_id=kwargs['sender_id']
        )
        if response_1b['success']:
            bridge['senderdoc'] = response_1b['document']
        else:
            self.logger.debug(
                'Invite funnel: sender user entity not found for %s; using neutral email copy',
                kwargs.get('sender_id'),
            )
            bridge['senderdoc'] = {}
        


        #1c. Get Portfolio document
        response_1c = self.get_entity(
            'portfolio',
            portfolio_id=kwargs['portfolio_id']
        )
        if not response_1c['success']:
            return response_1c
        else:
            bridge['portfoliodoc'] = response_1c['document']
        


        #2. Create Email to Hash rel 
        rel_data = {}
        rel_data['ttl'] = self.generate_ttl(offset_min=1440) 
        rel_data['email'] = kwargs['email']
        bridge['hash'] = rel_data['hash'] = self.generate_invite_hash(kwargs['email'],rel_data['ttl'])
        response_2 = self.create_rel('email:hash:ttl',**rel_data)

        self.logger.debug('Step 2: Creating Email to Hash to TTL relationship ')
        self.logger.debug(response_2)

        if not response_2['success']:
            response_2['message'] = 'Could not create Email to Hash to TTL relationship'                
            return response_2
        else:
            transaction.append(response_2)
            


        #3. Create Hash to Team rel 

        rel_data = {}
        rel_data['hash'] = bridge['hash']
        rel_data['team_id'] = kwargs['team_id']
        response_3 = self.create_rel('hash:team',**rel_data)

        self.logger.debug('Step 3: Creating Hash to Team relationship ')
        self.logger.debug(response_3)

        if not response_3['success']:
            response_3['message'] = 'Could not create Hash to Team relationship'                
            return response_3
        else:
            transaction.append(response_3)




        #4. Send email to invite recipient

        invite_sender = self._invite_from_address()

        sender_first = str(bridge['senderdoc'].get('name') or '').strip()
        sender_last = str(bridge['senderdoc'].get('slot_a') or '').strip()
        sender_full = (sender_first + ' ' + sender_last).strip()
        fe_base = self._invite_fe_base_url().rstrip('/')
        invite_url = (
            fe_base + '/invite?code=' + bridge['hash']
            + '&email=' + kwargs['email']
        )
        brand_name = globals().get('WL_NAME') or self.config.get('WL_NAME') or 'Noma'
        email_payload = self._build_invite_email(
            sender_name=sender_full,
            portfolio_name=bridge['portfoliodoc'].get('name', ''),
            team_name=bridge['teamdoc'].get('name', ''),
            code=bridge['hash'],
            invite_url=invite_url,
            brand_name=brand_name,
            locale=kwargs.get('locale', 'en'),
        )
        response_4 = self.AUM.send_email(
            sender=invite_sender,
            recipient=kwargs['email'],
            subject=email_payload['subject'],
            body_text=email_payload['body_text'],
            body_html=email_payload['body_html'],
        )
        if not response_4['success']:
            # Keep Resend's error detail; still return invite_url for manual copy.
            real_reason = response_4.get('message', 'Unknown error')
            self.logger.error('Invite funnel: send_email failed - %s', real_reason)
            response_4['message'] = f'Could not send the invite: {real_reason}'
            response_4['email_sent'] = False
            response_4['invite_url'] = invite_url
            return response_4
        else:
            response_4['message'] = (
                'Sent invite to team ' + kwargs['team_id'] + ' via email to ' + kwargs['email']
            )
            transaction.append(response_4)


        

        #All went good, Summarize Transaction Success 
        self.logger.debug('End of Funnel ')

        result['success'] = True
        result['message'] = 'Invite User Funnel completed, Ok'
        result['status'] = 200 
        result['document'] = transaction
        result['email_sent'] = True
        result['invite_url'] = invite_url

        self.logger.debug(result)            
        return result



    def send_existing_user_invite_email(
        self,
        email,
        team_id,
        portfolio_id,
        sender_id=None,
        locale='en',
    ):
        """
        Notify an existing Cognito/Dynamo user that they were added as a traveler.
        Uses a sign-in link (not /invite), since the accept-invite flow rejects
        emails that already exist in the user pool.
        """
        team_name = ''
        portfolio_name = ''
        sender_full = ''

        team_resp = self.get_entity('team', portfolio_id=portfolio_id, team_id=team_id)
        if team_resp.get('success'):
            team_name = (team_resp['document'] or {}).get('name', '') or ''

        portfolio_resp = self.get_entity('portfolio', portfolio_id=portfolio_id)
        if portfolio_resp.get('success'):
            portfolio_name = (portfolio_resp['document'] or {}).get('name', '') or ''

        if sender_id:
            sender_resp = self.get_entity('user', user_id=sender_id)
            if sender_resp.get('success'):
                doc = sender_resp['document'] or {}
                sender_full = (
                    (doc.get('name') or '').strip() + ' ' + (doc.get('slot_a') or '').strip()
                ).strip()

        fe_base = self._invite_fe_base_url().rstrip('/')
        from urllib.parse import quote
        invite_url = f"{fe_base}/login?email={quote(email or '')}"
        brand_name = globals().get('WL_NAME') or self.config.get('WL_NAME') or 'Noma'

        email_payload = self._build_invite_email(
            sender_name=sender_full,
            portfolio_name=portfolio_name,
            team_name=team_name,
            code='',
            invite_url=invite_url,
            brand_name=brand_name,
            locale=locale,
            existing_user=True,
        )
        response = self.AUM.send_email(
            sender=self._invite_from_address(),
            recipient=email,
            subject=email_payload['subject'],
            body_text=email_payload['body_text'],
            body_html=email_payload['body_html'],
        )
        return {
            "success": bool(response.get('success')),
            "message": response.get('message') or (
                'Invite email sent' if response.get('success') else 'Could not send the invite'
            ),
            "status": response.get('status') or (200 if response.get('success') else 400),
            "email_sent": bool(response.get('success')),
            "invite_url": invite_url,
        }




    def invite_create_user_funnel(self,**kwargs):
        bridge = {}
        result = {}
        transaction = []
        self.logger.debug('Initiating CREATE INVITE USER FUNNEL')



        #1. Check minimum requirements
        required_keys = ['code', 'email', 'pass']
        if not all(key in kwargs for key in required_keys):
            return {
                "success": False,
                "message": "Missing attributes",
                "status": 400,
            }

        #1b. Check if this user already exists in the pool. Cancel funnel if True
        response_1b = self.AUM.check_user_by_email(kwargs['email'])
        if response_1b['success']:
            return {
                "success": False,
                "message": "User already exists, sign in to access",
                "status": 404,
            }

        #2. Verify that the invitation code is valid
        response_2 = self._validate_invite_code(kwargs['email'], kwargs['code'])
        self.logger.debug('Step 2: Verifying that the invitation is valid ')
        self.logger.debug(response_2)

        if not response_2['success']:
            return response_2

        invite_doc = response_2['document']
        email = invite_doc['email']
        hash = invite_doc['hash']
        if not kwargs.get('first') or not kwargs.get('last'):
            kwargs['first'] = kwargs.get('first') or invite_doc.get('first') or ''
            kwargs['last'] = kwargs.get('last') or invite_doc.get('last') or ''
        if not kwargs['first']:
            kwargs['first'], kwargs['last'] = self._split_invitee_name('', kwargs['email'])

        transaction.append(response_2)

 
            

        #3. Create a stable new Cognito User
        #Input: email,first,last,pass

        #3a. Add user to cognito user pool
        response_3a = self.AUM.cognito_user_create(kwargs['email'], kwargs['first'], kwargs['last'])    
        self.logger.debug('Step 3a: Creating Cognito user ')
        self.logger.debug(response_3a)  
        if not response_3a['success']:
            response_3a['message'] = 'Could not create user in Identity Service'                
            return response_3a
        bridge['cognito_username'] = response_3a['document']['User']['Username']
        
        #3b. Assigned user password
        response_3b = self.AUM.cognito_user_permanent_password_assign(kwargs['email'],kwargs['pass'])
        self.logger.debug('Step 3a: Assign user password')
        self.logger.debug(response_3b)
        if not response_3b['success']:
            response_3a['message'] = 'Could not assigned provided password please reset your password'
            return response_3b
     
        transaction.append(response_3a)
        transaction.append(response_3b)


        #4. Create User Entity
        #Input: email, cognito_username, first, last
        data = {}
        bridge['user_id'] = data['user_id'] = create_md5_hash(bridge['cognito_username'],9)
        data['name'] = kwargs['first']
        data['slot_a'] = kwargs['last']
        data['email'] = kwargs['email']

        response_4 = self.create_entity('user',**data)
        self.logger.debug('Step 4: Create User Entity Document')
        if not response_4['success']:                      
            return response_4
        
        transaction.append(response_4)



        #5. Create the Rels that link the new user to the teams from the invitation
        # Run again the prefix search for all invitations to this email ( index = email:hash:ttl  prefix = email )
        index = 'irn:rel:email:hash:ttl:*:*:*'
        prefix = kwargs['email']+':'+kwargs['code']
        response_5 = self.AUM.list_rel_prefix(index,prefix) 
        teams_to_add = []
        if response_5['success'] and len(response_5['document'])>0 :

            
            for invitation in response_5['document']:
                email,hash,ttl = invitation['rel'].split(":")

                valid_invitations = []
                # TTL has not expired
                if  str(self.generate_ttl(0)) < str(ttl):
                    valid_invitations.append({'email':email,'hash':hash})
                else:
                    continue

                      
                for i in valid_invitations:                
                    response_5b = self.list_rel('hash:team',hash=hash)
                    #This response will only have one item

                    if not response_5b['success']:
                        self.logger.debug('Team for invitation:('+hash+') was not found')
                        continue

                    team_items = (response_5b.get('document') or {}).get('items') or []
                    if not team_items:
                        self.logger.debug('Team for invitation:('+hash+') returned no items')
                        continue

                    team_id = team_items[0]['rel']
                    teams_to_add.append(team_id)

        else:
            return{
                "success":False, 
                "message": "Could not find any invitation related to this email:code", 
                "status" :400
                }

        
        for team in teams_to_add:

            # Create Team to User Rel
            rel_data = {}
            rel_data['user_id'] = bridge['user_id']
            rel_data['team_id'] = team #This is the team_id
            response_5c = self.create_rel('team:user',**rel_data)

            self.logger.debug('Step 5c:Creating Team-User relationship')
            self.logger.debug(response_5c)

            if not response_5c['success']:
                response_5c['message'] = 'Could not create Team-User relationship'                
                continue
            else:
                transaction.append(response_5c)

            
            # Create User to Team Rel (Adding the owner as the first member of the team)
            rel_data = {}
            rel_data['user_id'] = bridge['user_id']
            rel_data['team_id'] = team #This is the team_id
            response_5d = self.create_rel('user:team',**rel_data)

            self.logger.debug('Step 5d: Creating User to Team relationship ')
            self.logger.debug(response_5d)

            if not response_5d['success']:
                response_5d['message'] = 'Could not create User-Team relationship'                
                continue
            else:
                transaction.append(response_5d)



        #6. Send signal to FE to show "Account Created" message and redirect to loing page 
        # OPTIONALLY (SKIP): You could  programmatically login the user and send access tokens in the response 
        # That way they new user gets immediate access without having to go to the login page.
        # The problem is that you wouldn't show the user how to sing in. 

        #Returning status:200 is enough for FE to decide what to do next (redirect to sing in page)



        #All went good, Summarize Transaction Success 
        self.logger.debug('End of Funnel ')

        result['success'] = True
        result['message'] = 'Create Invite User Funnel completed, Ok'
        result['status'] = 200 
        result['document'] = transaction  

        self.logger.debug(result)            
        return result

    

    def remove_org_funnel(self,**kwargs):
        result = {}
        transaction = []

        self.logger.debug('Initiating DELETE ORG FUNNEL')

        #0. Check minimum requirements
        required_keys = ['portfolio_id','org_id']
        if not all(key in kwargs for key in required_keys):
            return{
            "success":False, 
            "message": "Missing attributes", 
            "status" :400
            }

        #1. Retrieve org document to be unlinked
        response_1a = self.get_entity(
                    'org',
                    portfolio_id=kwargs['portfolio_id'],
                    org_id=kwargs['org_id']
                    )

        if not response_1a['success']:
            return{
            "success":False, 
            "message": "Org not found", 
            "status" :400
            }

        orgdoc = response_1a['document']

        #2. Remove team:org and team/tool:org rels referencing this org.
        # There is no reverse (org->team) index, so we walk every team in the
        # portfolio and drop the rels that point at this org, if any exist.
        teams_index = 'irn:entity:portfolio/team:' + kwargs['portfolio_id'] + ':*'
        teams_resp = self.AUM.list_entity(teams_index, limit=100)
        team_items = (teams_resp or {}).get('document', {}).get('items', []) or []

        for team in team_items:
            team_id = team.get('_id')
            if not team_id:
                continue

            #2a. Remove team:org rel, if it exists
            rel_check = self.get_rel('team:org', team_id=team_id, org_id=kwargs['org_id'])
            if rel_check and rel_check.get('success'):
                response_2a = self.delete_rel('team:org', team_id=team_id, org_id=kwargs['org_id'])
                if not response_2a['success']:
                    response_2a['message'] = 'Could not remove Team-Org relationship'
                    return response_2a
                transaction.append(response_2a)

            #2b. Remove team/tool:org rels for every tool this team has, if any exist
            tool_rels = self.list_rel('team:tool', team_id=team_id)
            tool_items = (tool_rels or {}).get('document', {}).get('items', []) or []
            for tool_item in tool_items:
                tool_id = tool_item.get('rel')
                if not tool_id:
                    continue
                rel_check_2 = self.get_rel(
                        'team/tool:org',
                        team_id=team_id,
                        tool_id=tool_id,
                        org_id=kwargs['org_id']
                        )
                if rel_check_2 and rel_check_2.get('success'):
                    response_2b = self.delete_rel(
                            'team/tool:org',
                            team_id=team_id,
                            tool_id=tool_id,
                            org_id=kwargs['org_id']
                            )
                    if not response_2b['success']:
                        response_2b['message'] = 'Could not remove Team/Tool-Org relationship'
                        return response_2b
                    transaction.append(response_2b)

        #3. Unlink the org entity itself (soft delete, like remove_team_funnel)
        response_3 = self.unlink_entity(**orgdoc)
        if not response_3['success']:
            response_3['message'] = 'Could not remove Org'
            return response_3
        transaction.append(response_3)

        #All went good, Summarize Transaction Success 
        self.logger.debug('End of Funnel ')

        result['success'] = True
        result['message'] = 'Delete Org Funnel completed, Ok'
        result['status'] = 200 
        result['document'] = transaction  

        self.logger.debug(result)            
        return result
    


    def remove_team_funnel(self,**kwargs):
        bridge = {}
        result = {}
        transaction = []

        self.logger.debug('Initiating DELETE TEAM FUNNEL')


        #0. Check minimum requirements
        required_keys = ['portfolio_id','team_id'] 
        if not all(key in kwargs for key in required_keys):
            return{
            "success":False, 
            "message": "Missing attributes", 
            "status" :400
            }
        
        #1. Unlink document

        #1a. Retrieve document to be unlinked
        response_1a = self.get_entity(
                    'team',
                    portfolio_id=kwargs['portfolio_id'],
                    team_id=kwargs['team_id']
                    )
            
        if not response_1a['success']:
            return{
            "success":False, 
            "message": "Team not found", 
            "status" :400
            }
                
        #1b. Send document to unlink function
        teamdoc = response_1a['document'] 
        response_1b = self.unlink_entity(**teamdoc)  
        if not response_1b['success']:
            return response_1b
         
        transaction.append(response_1b)



        #2. Remove team-portfolio rels
        # Remove rel team:portfolio 
        # You need to list the rels first and then eliminate one at a time
        response_2a = self.list_rel(
                'team:portfolio',
                team_id=kwargs['team_id']
                )
        
        for portfolio in response_2a['document']['items']:

            response_2aa = self.delete_rel(
                    'team:portfolio',
                    team_id=kwargs['team_id'],
                    portfolio_id=portfolio['rel']
                    )
            if not response_2aa['success']:
                response_2aa['message'] = 'Could not remove Team-Portfolio relationship'
                return response_2aa                 
            else:
                transaction.append(response_2aa)


        #3. Remove team-user rels
        # You need to list the rels first and then eliminate one at a time
        response_3 = self.list_rel(
                'team:user',
                team_id=kwargs['team_id']
                )
        
        for user in response_3['document']['items']:

            response_3a = self.delete_rel(
                    'team:user',
                    team_id=kwargs['team_id'],
                    user_id=user['rel']
                    )   
            if not response_3a['success']:
                response_3a['message'] = 'Could not remove Team-User relationship'
                return response_3a                 
            else:
                transaction.append(response_3a)

            
            response_3b = self.delete_rel(
                    'user:team',
                    team_id=kwargs['team_id'],
                    user_id=user['rel']
                    )   
            if not response_3b['success']:
                response_3b['message'] = 'Could not remove User-Team relationship'
                return response_3b                 
            else:
                transaction.append(response_3b)


        #4. Remove team-org rels
        response_4 = self.list_rel(
                'team:org',
                team_id=kwargs['team_id']
                )
        
        for org in response_4['document']['items']:

            response_4a = self.delete_rel(
                    'team:org',
                    team_id=kwargs['team_id'],
                    user_id=org['rel']
                    )   
            if not response_4a['success']:
                response_4a['message'] = 'Could not remove Team-Org relationship'
                return response_4a                 
            else:
                transaction.append(response_4a)


        #All went good, Summarize Transaction Success 
        self.logger.debug('End of Funnel ')

        result['success'] = True
        result['message'] = 'Delete Team Funnel completed, Ok'
        result['status'] = 200 
        result['document'] = transaction  

        self.logger.debug(result)            
        return result
    
    

    def remove_user_from_team_funnel(self,**kwargs):
        bridge = {}
        result = {}
        transaction = []

        self.logger.debug('Initiating REMOVE TEAM USER FUNNEL')


        #0. Check minimum requirements
        required_keys = ['team_id','user_id'] 
        if not all(key in kwargs for key in required_keys):
            return{
            "success":False, 
            "message": "Missing attributes", 
            "status" :400
            }


        #1. Remove rels
        #1a. Remove rel team:user 
        response_1a = self.delete_rel(
                'team:user',
                team_id=kwargs['team_id'],
                user_id=kwargs['user_id']
                )   
        if not response_1a['success']:
            response_1a['message'] = 'Could not remove Team-User relationship'
            return response_1a                 
        else:
            transaction.append(response_1a)

        
        response_1b = self.delete_rel(
                'user:team',
                team_id=kwargs['team_id'],
                user_id=kwargs['user_id']
                )   
        if not response_1b['success']:
            response_1b['message'] = 'Could not remove Team-User relationship'                 
            return response_1b
        else:
            transaction.append(response_1b)

  
        #All went good, Summarize Transaction Success 
        self.logger.debug('End of Funnel')

        result['success'] = True
        result['message'] = 'Remove Team User funnel completed, Ok'
        result['status'] = 200 
        result['document'] = transaction  

        self.logger.debug(result)            
        return result


    def create_tool_funnel(self,**kwargs):
        result = {}
        transaction = []

        self.logger.debug('Initiating CREATE TOOL FUNNEL')

        #1. Create a Tool instance entity
        response_1 = self.create_entity('tool',**kwargs)

        self.logger.debug('Step 1: Installing tool in team')
        self.logger.debug(response_1)
        
        if not response_1['success']:
            response_1['message'] = 'Could not install Tool'                
            return response_1 
        else:
            transaction.append(response_1)


        #All went good, Summarize Transaction Success 
        self.logger.debug('End of Funnel ')

        result['success'] = True
        result['message'] = 'Create Tool Funnel completed, Ok'
        result['status'] = 200
        result['document'] = transaction

        self.logger.debug(result)
        return result
    
    
    def remove_tool_funnel(self,**kwargs):
        bridge = {}
        result = {}
        transaction = []

        self.logger.debug('Initiating DELETE TOOL FUNNEL')


        #0. Check minimum requirements
        required_keys = ['portfolio_id','tool_id'] 
        if not all(key in kwargs for key in required_keys):
            return{
            "success":False, 
            "message": "Missing attributes", 
            "status" :400
            }
        
        #1. Unlink document

        #1a. Retrieve document to be unlinked
        response_1a = self.get_entity(
                    'tool',
                    portfolio_id=kwargs['portfolio_id'],
                    tool_id=kwargs['tool_id']
                    )
            
        if not response_1a['success']:
            return{
            "success":False, 
            "message": "Tool not found", 
            "status" :400
            }
                
        #1b. Send document to unlink function
        tooldoc = response_1a['document'] 
        response_1b = self.unlink_entity(**tooldoc)  
        if not response_1b['success']:
            return response_1b
         
        transaction.append(response_1b)


        '''
        #2. Remove team-tool rels
        # You need to list the rels first and then eliminate one at a time
        response_2a = self.list_rel(
                'team:tool',
                team_id=kwargs['team_id']
                )
        
        for item in response_2a['document']['items']:

            response_2aa = self.delete_rel(
                    'team:tool',
                    team_id=kwargs['team_id'],
                    tool_id=item['rel']
                    )
            if not response_2aa['success']:
                response_2aa['message'] = 'Could not remove Team-Tool relationship'
                return response_2aa                 
            else:
                transaction.append(response_2aa)
        '''


        '''
        #3. Remove team-tool-role rel
        # You need to list the rels first and then eliminate one at a time
        response_3 = self.list_rel(
                'team/tool:role',
                team_id=kwargs['team_id'],
                tool_id=kwargs['tool_id']
                )
        
        for item in response_3['document']['items']:

            response_3a = self.delete_rel(
                    'team/tool:role',
                    team_id=kwargs['team_id'],
                    tool_id=kwargs['tool_id'],
                    role_id=item['rel']
                    )   
            if not response_3a['success']:
                response_3a['message'] = 'Could not remove Team-Tool-Role relationship'
                return response_3a                 
            else:
                transaction.append(response_3a)
        '''


        '''
        #4. Remove team-tool-org rels
        response_4 = self.list_rel(
                'team/tool:org',
                team_id=kwargs['team_id'],
                tool_id=kwargs['tool_id']
                )
        
        for item in response_4['document']['items']:

            response_4a = self.delete_rel(
                    'team/tool:org',
                    team_id=kwargs['team_id'],
                    tool_id=kwargs['tool_id'],
                    org_id=item['rel']
                    )   
            if not response_4a['success']:
                response_4a['message'] = 'Could not remove Team-Tool-Org relationship'
                return response_4a                 
            else:
                transaction.append(response_4a)
        '''



        #All went good, Summarize Transaction Success 
        self.logger.debug('End of Funnel ')

        result['success'] = True
        result['message'] = 'Delete Tool Funnel completed, Ok'
        result['status'] = 200 
        result['document'] = transaction  

        self.logger.debug(result)            
        return result
    
    
    
    def assign_team_tools(self,**kwargs):
        
        result = {}
        #Check minimum requirements:
        required_keys = ['team_id','tool_id'] 
        if not all(key in kwargs for key in required_keys):
            result['success'] = False
            result['message'] = 'Missing attributes' 
            result['status'] = 400             
            return result
        
            
        reltype = 'team:tool'
        if kwargs['method'] == 'POST':
            response = self.create_rel(
                reltype,
                team_id=kwargs['team_id'],
                tool_id=kwargs['tool_id']
                )
        elif kwargs['method'] == 'DELETE':
            response = self.delete_rel(
                reltype,
                team_id=kwargs['team_id'],
                tool_id=kwargs['tool_id']
                )
            
        return response
    
    
    def assign_team_tool_roles(self,**kwargs):
        
        result = {}
        #Check minimum requirements:
        required_keys = ['team_id','tool_id','role_id'] 
        if not all(key in kwargs for key in required_keys):
            result['success'] = False
            result['message'] = 'Missing attributes' 
            result['status'] = 400             
            return result
        
        
        reltype = 'team/tool:role'
        if kwargs['method'] == 'POST':
            response = self.create_rel(
                reltype,
                team_id=kwargs['team_id'],
                tool_id=kwargs['tool_id'],
                role_id=kwargs['role_id']
                )
        elif kwargs['method'] == 'DELETE':
            response = self.delete_rel(
                reltype,
                team_id=kwargs['team_id'],
                tool_id=kwargs['tool_id'],
                role_id=kwargs['role_id']
                )
            
        return response
    
    
    def assign_team_tool_orgs(self,**kwargs):
        
        result = {}
        #Check minimum requirements:
        required_keys = ['team_id','tool_id','org_id'] 
        if not all(key in kwargs for key in required_keys):
            result['success'] = False
            result['message'] = 'Missing attributes' 
            result['status'] = 400             
            return result
        
        
        reltype = 'team/tool:org'
        if kwargs['method'] == 'POST':
            response = self.create_rel(
                reltype,
                team_id=kwargs['team_id'],
                tool_id=kwargs['tool_id'],
                org_id=kwargs['org_id']
                )
        elif kwargs['method'] == 'DELETE':
            response = self.delete_rel(
                reltype,
                team_id=kwargs['team_id'],
                tool_id=kwargs['tool_id'],
                org_id=kwargs['org_id']
                )
            
        return response



        
    