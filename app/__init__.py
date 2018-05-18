import re
from functools import wraps

from flask import Flask, request, redirect, session, abort
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect, CSRFError

import dmapiclient
from dmutils import init_app, flask_featureflags
from dmutils.user import User
from dmutils.timing import logged_duration

from config import configs

data_api_client = dmapiclient.DataAPIClient()
login_manager = LoginManager()
feature_flags = flask_featureflags.FeatureFlag()
csrf = CSRFProtect()


from app.main.helpers.services import parse_document_upload_time
from app.main.helpers.frameworks import question_references


def create_app(config_name):
    application = Flask(__name__,
                        static_folder='static/',
                        static_url_path=configs[config_name].STATIC_URL_PATH)

    init_app(
        application,
        configs[config_name],
        data_api_client=data_api_client,
        feature_flags=feature_flags,
        login_manager=login_manager,
    )

    from .main import main as main_blueprint
    from .status import status as status_blueprint
    from dmutils.external import external as external_blueprint

    application.register_blueprint(main_blueprint, url_prefix='/suppliers')
    application.register_blueprint(status_blueprint, url_prefix='/suppliers')

    # Must be registered last so that any routes declared in the app are registered first (i.e. take precedence over
    # the external NotImplemented routes in the dm-utils external blueprint).
    application.register_blueprint(external_blueprint)

    login_manager.login_message_category = "must_login"
    main_blueprint.config = application.config.copy()

    csrf.init_app(application)

    @application.errorhandler(CSRFError)
    def csrf_handler(reason):
        if 'user_id' not in session:
            application.logger.info(
                u'csrf.session_expired: Redirecting user to log in page'
            )

            return application.login_manager.unauthorized()

        application.logger.info(
            u'csrf.invalid_token: Aborting request, user_id: {user_id}',
            extra={'user_id': session['user_id']})

        abort(400, reason)

    @application.before_request
    def remove_trailing_slash():
        if request.path.endswith('/'):
            return redirect(request.path[:-1], code=301)

    @application.before_request
    def refresh_session():
        session.permanent = True
        session.modified = True

    application.add_template_filter(question_references)
    application.add_template_filter(parse_document_upload_time)

    def log_duration(method_name, fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            with logged_duration(message="Call to `{}` finished in {{duration_real}}s of real-time".format(method_name),
                                 condition=None):
                return fn(*args, **kwargs)

        return wrapper

    data_api_client_method_names = [x for x in dmapiclient.DataAPIClient.__dict__.keys() if callable(getattr(dmapiclient.DataAPIClient, x))]
    for method_name in data_api_client_method_names:
        setattr(data_api_client, method_name, log_duration(method_name, getattr(data_api_client, method_name)))

    return application


@login_manager.user_loader
def load_user(user_id):
    return User.load_user(data_api_client, user_id)


def config_attrs(config):
    """Returns config attributes from a Config object"""
    p = re.compile('^[A-Z_]+$')
    return filter(lambda attr: bool(p.match(attr)), dir(config))
